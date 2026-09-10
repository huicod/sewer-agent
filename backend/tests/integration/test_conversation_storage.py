"""Opt-in durable conversation storage tests using a disposable PostgreSQL schema."""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest
from pipe_inspector.config import Settings
from pipe_inspector.contracts.assets import AssetKind, AssetRecord, AssetRef
from pipe_inspector.contracts.intake import TaskSubmission
from pipe_inspector.contracts.turns import TurnSubmission
from pipe_inspector.storage.conversation import ConversationBusy, ConversationRepository
from pipe_inspector.storage.repository import MissingRecord, SubmissionConflict
from psycopg import sql
from pydantic import SecretStr

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("SEWER_RUN_STORAGE_TEST") != "1",
        reason="explicit opt-in required: uses the local PostgreSQL application database",
    ),
]


@pytest.fixture
def conversation_store():
    settings = Settings()
    schema = "test_conversation_storage_" + uuid4().hex
    with psycopg.connect(settings.database_dsn.get_secret_value()) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        store = ConversationRepository(settings.database_dsn, schema=schema)
        store.initialize()
        store.initialize()
        yield store, settings, schema
    finally:
        with psycopg.connect(settings.database_dsn.get_secret_value()) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )


def save_original_asset(store, *, kind=AssetKind.IMAGE):
    kwargs = {"duration_seconds": 1.0} if kind == AssetKind.VIDEO else {}
    asset = AssetRecord(
        ref=AssetRef(asset_id=uuid4()),
        kind=kind,
        media_type="video/mp4" if kind == AssetKind.VIDEO else "image/png",
        byte_size=128,
        width=16,
        height=12,
        **kwargs,
    )
    store.save_asset(asset)
    return asset


def create_task(store, session_id, asset):
    return store.create_task(
        TaskSubmission(
            request_id=uuid4(),
            session_id=session_id,
            input_asset=asset.ref,
            instruction="inspect",
        )
    )


def submission(session_id, **changes):
    return TurnSubmission.model_validate(
        {
            "request_id": uuid4(),
            "session_id": session_id,
            "text": "Inspect this asset",
        }
        | changes
    )


def test_admission_reopens_and_identical_request_keeps_first_config(conversation_store):
    store, settings, schema = conversation_store
    session = store.create_session()
    turn = submission(session.session_id)
    first_config = {"workflow_version": "conversation-v1", "attempt_limit": 2}

    admitted = store.admit_turn(turn, first_config)
    retried = ConversationRepository(settings.database_dsn, schema=schema).admit_turn(
        turn, {"workflow_version": "changed"}
    )

    assert retried == admitted
    assert retried.status == "queued"
    assert store.get_turn(turn.request_id) == admitted
    assert store.list_turns(session.session_id) == (admitted,)
    assert store.get_turn_config(turn.request_id) == first_config


def test_admission_rejects_request_identity_mismatch(conversation_store):
    store, _, _ = conversation_store
    session = store.create_session()
    request_id = uuid4()
    store.admit_turn(
        TurnSubmission(request_id=request_id, session_id=session.session_id, text="first"), {}
    )

    with pytest.raises(SubmissionConflict, match="^submission conflicts with stored state$"):
        store.admit_turn(
            TurnSubmission(request_id=request_id, session_id=session.session_id, text="changed"),
            {},
        )


def test_admission_serializes_each_session_but_allows_separate_sessions(
    conversation_store,
):
    store, settings, schema = conversation_store
    session = store.create_session()
    workers = 4
    barrier = Barrier(workers)

    def admit_once(index):
        reopened = ConversationRepository(settings.database_dsn, schema=schema)
        candidate = submission(session.session_id, text=f"turn {index}")
        barrier.wait(timeout=10)
        try:
            return reopened.admit_turn(candidate, {"index": index})
        except ConversationBusy:
            return None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        admitted = list(pool.map(admit_once, range(workers)))

    assert sum(turn is not None for turn in admitted) == 1
    other_session = store.create_session()
    other = store.admit_turn(submission(other_session.session_id), {})
    assert other.session_id == other_session.session_id


def test_admission_validates_task_asset_kind_and_session_ownership(conversation_store):
    store, _, _ = conversation_store
    local_session = store.create_session()
    foreign_session = store.create_session()
    local_asset = save_original_asset(store)
    foreign_asset = save_original_asset(store)
    local_task = create_task(store, local_session.session_id, local_asset)
    foreign_task = create_task(store, foreign_session.session_id, foreign_asset)
    derived = AssetRecord(
        ref=AssetRef(asset_id=uuid4()),
        kind=AssetKind.CROP,
        media_type="image/png",
        byte_size=32,
        width=8,
        height=6,
        parent=local_asset.ref,
    )
    store.save_asset(derived)

    invalid = [
        submission(local_session.session_id, input_asset=AssetRef(asset_id=uuid4())),
        submission(local_session.session_id, input_asset=derived.ref),
        submission(local_session.session_id, input_asset=foreign_asset.ref),
        submission(local_session.session_id, task_id=foreign_task.snapshot.task_id),
        submission(
            local_session.session_id,
            task_id=local_task.snapshot.task_id,
            input_asset=foreign_asset.ref,
        ),
    ]
    for candidate in invalid:
        with pytest.raises((MissingRecord, SubmissionConflict)):
            store.admit_turn(candidate, {})

    accepted = store.admit_turn(
        submission(
            local_session.session_id,
            task_id=local_task.snapshot.task_id,
            input_asset=local_asset.ref,
        ),
        {},
    )
    assert accepted.task_id == local_task.snapshot.task_id


def test_list_turns_returns_chronological_tail_and_checks_session(conversation_store):
    store, _, _ = conversation_store
    session = store.create_session()
    records = []
    for index in range(3):
        record = store.admit_turn(submission(session.session_id, text=f"turn {index}"), {})
        records.append(
            store.update_turn(
                record.request_id,
                status="completed",
                stage="finished",
                reply=f"reply {index}",
            )
        )

    assert store.list_turns(session.session_id, limit=2) == tuple(records[-2:])
    with pytest.raises(ValueError, match="limit must be between 1 and 100"):
        store.list_turns(session.session_id, limit=0)
    with pytest.raises(MissingRecord, match="^record not found$"):
        store.list_turns(uuid4())


def test_update_preserves_submission_references_and_terminal_state(conversation_store):
    store, _, _ = conversation_store
    session = store.create_session()
    asset = save_original_asset(store)
    task = create_task(store, session.session_id, asset)
    original = store.admit_turn(submission(session.session_id, input_asset=asset.ref), {})

    running = store.update_turn(
        original.request_id, status="running", stage="executing", attempts=2
    )
    assert running.request_id == original.request_id
    assert running.session_id == original.session_id
    assert running.text == original.text
    assert running.input_asset == original.input_asset
    assert running.attempts == 2
    assert store.update_turn(original.request_id, attempts=1).attempts == 2

    completed = store.update_turn(
        original.request_id,
        status="completed",
        stage="finished",
        reply="done",
        linked_task_id=task.snapshot.task_id,
    )
    assert store.update_turn(completed.request_id, status="running") == completed
    assert store.get_turn(completed.request_id) == completed

    with pytest.raises(ValueError, match="unsupported turn fields"):
        store.update_turn(completed.request_id, text="replace immutable submission")


def test_update_rejects_cross_session_linked_task(conversation_store):
    store, _, _ = conversation_store
    session = store.create_session()
    foreign_session = store.create_session()
    foreign_asset = save_original_asset(store)
    foreign_task = create_task(store, foreign_session.session_id, foreign_asset)
    turn = store.admit_turn(submission(session.session_id), {})

    with pytest.raises(SubmissionConflict, match="^submission conflicts with stored state$"):
        store.update_turn(turn.request_id, linked_task_id=foreign_task.snapshot.task_id)


def test_turn_checkpoints_are_first_writer_cached_across_reopen(conversation_store):
    store, settings, schema = conversation_store
    session = store.create_session()
    turn = store.admit_turn(submission(session.session_id), {})

    first = store.save_turn_step(turn.request_id, "intent", {"kind": "inspect"})
    replayed = ConversationRepository(settings.database_dsn, schema=schema).save_turn_step(
        turn.request_id, "intent", {"kind": "ask"}
    )

    assert first == {"kind": "inspect"}
    assert replayed == first
    assert store.load_turn_step(turn.request_id, "intent") == first
    assert store.load_turn_step(turn.request_id, "missing") is None


def test_concurrent_turn_checkpoint_retries_keep_one_complete_payload(conversation_store):
    store, settings, schema = conversation_store
    session = store.create_session()
    turn = store.admit_turn(submission(session.session_id), {})
    workers = 4
    barrier = Barrier(workers)

    def save_once(index):
        reopened = ConversationRepository(settings.database_dsn, schema=schema)
        barrier.wait(timeout=10)
        return reopened.save_turn_step(turn.request_id, "plan", {"winner": index})

    with ThreadPoolExecutor(max_workers=workers) as pool:
        saved = list(pool.map(save_once, range(workers)))

    assert saved == [saved[0]] * workers
    assert store.load_turn_step(turn.request_id, "plan") == saved[0]


def test_storage_errors_and_repository_repr_do_not_expose_credentials(conversation_store):
    store, _, _ = conversation_store
    for action in (
        lambda: store.get_turn(uuid4()),
        lambda: store.get_turn_config(uuid4()),
        lambda: store.save_turn_step(uuid4(), "intent", {}),
        lambda: store.update_turn(uuid4(), status="running"),
    ):
        with pytest.raises(MissingRecord) as caught:
            action()
        assert str(caught.value) == "record not found"

    marker = "unique-secret-marker"
    unavailable = ConversationRepository(
        SecretStr(f"postgresql://user:{marker}@127.0.0.1:1/database")
    )
    with pytest.raises(psycopg.OperationalError) as caught:
        unavailable.get_turn(uuid4())
    assert marker not in repr(unavailable)
    assert marker not in str(caught.value)
