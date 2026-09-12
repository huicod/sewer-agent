"""Opt-in atomic saved-comparison tests in a disposable PostgreSQL schema."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest
from pipe_inspector.comparison import ComparisonNotAllowed
from pipe_inspector.config import Settings
from pipe_inspector.contracts.assets import AssetKind, AssetRecord, AssetRef
from pipe_inspector.contracts.findings import EvidenceRef, Finding
from pipe_inspector.contracts.intake import TaskSubmission
from pipe_inspector.contracts.tasks import TaskSnapshot
from pipe_inspector.contracts.turns import TurnSubmission
from pipe_inspector.storage.conversation import ConversationRepository
from pipe_inspector.storage.repository import MissingRecord, SubmissionConflict
from psycopg import sql
from psycopg.types.json import Jsonb

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("SEWER_RUN_STORAGE_TEST") != "1",
        reason="explicit opt-in required: uses the local PostgreSQL application database",
    ),
]


@pytest.fixture
def comparison_store():
    settings = Settings()
    schema = "test_comparison_storage_" + uuid4().hex
    with psycopg.connect(settings.database_dsn.get_secret_value()) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        store = ConversationRepository(settings.database_dsn, schema=schema)
        store.initialize()
        yield store, settings, schema
    finally:
        with psycopg.connect(settings.database_dsn.get_secret_value()) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )


def save_original(store, kind=AssetKind.IMAGE):
    asset = AssetRecord(
        ref=AssetRef(asset_id=uuid4()),
        kind=kind,
        media_type="video/mp4" if kind is AssetKind.VIDEO else "image/png",
        byte_size=128,
        width=16,
        height=12,
        duration_seconds=1.0 if kind is AssetKind.VIDEO else None,
    )
    store.save_asset(asset)
    return asset


def save_task(store, session_id, asset, *, ready=True, confirmed=0):
    task = store.create_task(
        TaskSubmission(
            request_id=uuid4(),
            session_id=session_id,
            input_asset=asset.ref,
            instruction="inspect source",
        )
    )
    if not ready:
        return task
    findings = tuple(
        Finding(
            finding_id=uuid4(),
            task_id=task.snapshot.task_id,
            canonical_label="AJ",
            review_status="confirmed",
            source="detector",
            evidence=(EvidenceRef(asset=asset.ref),),
            confirmed_by="rules",
        )
        for _ in range(confirmed)
    )
    snapshot = TaskSnapshot(
        task_id=task.snapshot.task_id,
        runtime_status="completed",
        inspection_result="defects_found" if findings else "normal",
        stage="finished",
        stage_progress=1,
        overall_progress=1,
        updated_at=datetime.now(timezone.utc),
        findings=findings,
        coverage={
            "sufficient_coverage": True,
            "sufficient_quality": True,
            "negative_audit_complete": not findings,
        },
    )
    return store.update_snapshot(snapshot)


def admit_compare(
    store, session_id, reference, current, *, request_id=None, bind_reference=True
):
    submission = TurnSubmission(
        request_id=request_id or uuid4(),
        session_id=session_id,
        text="compare these saved inspections",
        reference_task_id=reference.snapshot.task_id if bind_reference else None,
        task_id=current.snapshot.task_id,
    )
    turn = store.admit_turn(submission, {"workflow_version": "conversation-v6"})
    return store.update_turn(turn.request_id, status="running", stage="executing")


def comparison_case(
    store, *, current_kind=AssetKind.IMAGE, current_ready=True, bind_reference=True
):
    session = store.create_session()
    reference_media = save_original(store)
    current_media = save_original(store, current_kind)
    reference = save_task(store, session.session_id, reference_media, confirmed=1)
    current = save_task(
        store,
        session.session_id,
        current_media,
        ready=current_ready,
        confirmed=2,
    )
    turn = admit_compare(
        store,
        session.session_id,
        reference,
        current,
        bind_reference=bind_reference,
    )
    return turn, reference, current, reference_media, current_media


def test_save_comparison_snapshots_sources_without_mutating_them(comparison_store):
    store, _, _ = comparison_store
    turn, reference, current, reference_media, current_media = comparison_case(store)

    saved = store.save_comparison(
        turn.request_id,
        reference.snapshot.task_id,
        current.snapshot.task_id,
        attempt=2,
    )

    assert saved["request_id"] == str(turn.request_id)
    assert saved["attempt"] == 2
    assert saved["reference"] == reference.model_dump(mode="json")
    assert saved["current"] == current.model_dump(mode="json")
    assert saved["reference_media"] == reference_media.model_dump(mode="json")
    assert saved["current_media"] == current_media.model_dump(mode="json")
    assert saved["rows"][0]["confirmed_delta"] == 1
    assert len(saved["rows"]) == 7
    assert store.load_turn_step(turn.request_id, "comparison") == saved
    assert store.get_task(reference.snapshot.task_id) == reference
    assert store.get_task(current.snapshot.task_id) == current
    assert store.get_asset(reference_media.ref) == reference_media
    assert store.get_asset(current_media.ref) == current_media


def test_cached_comparison_is_exact_and_does_not_reread_changed_sources(
    comparison_store,
):
    store, settings, schema = comparison_store
    turn, reference, current, _, _ = comparison_case(store)
    saved = store.save_comparison(
        turn.request_id, reference.snapshot.task_id, current.snapshot.task_id
    )
    changed = current.model_dump(mode="json") | {"instruction": "changed later"}
    with store._connect() as connection:
        connection.execute(
            sql.SQL("UPDATE {} SET record = %s WHERE task_id = %s").format(
                store._table("sewer_tasks")
            ),
            (Jsonb(changed), current.snapshot.task_id),
        )

    replayed = ConversationRepository(settings.database_dsn, schema=schema).save_comparison(
        turn.request_id,
        reference.snapshot.task_id,
        current.snapshot.task_id,
        attempt=99,
    )

    assert replayed == saved
    assert replayed["current"]["instruction"] == "inspect source"


def test_malformed_cached_comparison_is_not_silently_replaced(comparison_store):
    store, _, _ = comparison_store
    turn, reference, current, _, _ = comparison_case(store)
    with store._connect() as connection:
        connection.execute(
            sql.SQL("UPDATE {} SET checkpoints = %s WHERE request_id = %s").format(
                store._table("sewer_conversation_turns")
            ),
            (Jsonb({"comparison": None}), turn.request_id),
        )

    with pytest.raises(SubmissionConflict):
        store.save_comparison(
            turn.request_id, reference.snapshot.task_id, current.snapshot.task_id
        )
    with store._connect() as connection:
        checkpoints = connection.execute(
            sql.SQL("SELECT checkpoints FROM {} WHERE request_id = %s").format(
                store._table("sewer_conversation_turns")
            ),
            (turn.request_id,),
        ).fetchone()[0]
    assert checkpoints == {"comparison": None}


def test_concurrent_comparison_attempts_keep_one_complete_winner(comparison_store):
    store, settings, schema = comparison_store
    turn, reference, current, _, _ = comparison_case(store)
    barrier = Barrier(4)

    def save_once(attempt):
        reopened = ConversationRepository(settings.database_dsn, schema=schema)
        barrier.wait(timeout=10)
        return reopened.save_comparison(
            turn.request_id,
            reference.snapshot.task_id,
            current.snapshot.task_id,
            attempt=attempt,
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        saved = list(pool.map(save_once, range(1, 5)))

    assert saved == [saved[0]] * 4
    assert saved[0]["attempt"] in {1, 2, 3, 4}


def test_save_comparison_rejects_missing_foreign_and_body_identity_conflicts(
    comparison_store,
):
    store, _, _ = comparison_store
    turn, reference, current, _, _ = comparison_case(store, bind_reference=False)
    foreign_session = store.create_session()
    foreign_media = save_original(store)
    foreign = save_task(store, foreign_session.session_id, foreign_media)
    other_local_media = save_original(store)
    other_local = save_task(store, turn.session_id, other_local_media)

    for source_id in (uuid4(), foreign.snapshot.task_id):
        with pytest.raises(MissingRecord):
            store.save_comparison(
                turn.request_id, source_id, current.snapshot.task_id
            )
    with pytest.raises(SubmissionConflict):
        store.save_comparison(
            turn.request_id, reference.snapshot.task_id, other_local.snapshot.task_id
        )


@pytest.mark.parametrize(
    ("case", "code"),
    [
        ("same", "comparison_same_task"),
        ("pending", "comparison_source_not_ready"),
        ("media", "comparison_media_mismatch"),
    ],
)
def test_save_comparison_rejects_disallowed_source_pairs(
    comparison_store, case, code
):
    store, _, _ = comparison_store
    if case == "pending":
        turn, reference, current, _, _ = comparison_case(store, current_ready=False)
    elif case == "media":
        turn, reference, current, _, _ = comparison_case(
            store, current_kind=AssetKind.VIDEO
        )
    else:
        session = store.create_session()
        media = save_original(store)
        reference = save_task(store, session.session_id, media)
        current = reference
        turn = admit_compare(store, session.session_id, reference, current)

    with pytest.raises(ComparisonNotAllowed) as caught:
        store.save_comparison(
            turn.request_id, reference.snapshot.task_id, current.snapshot.task_id
        )
    assert caught.value.code == code


def test_first_comparison_write_requires_an_active_turn(comparison_store):
    store, _, _ = comparison_store
    turn, reference, current, _, _ = comparison_case(store)
    store.update_turn(
        turn.request_id,
        status="completed",
        stage="finished",
        reply="closed before comparison",
    )

    with pytest.raises(SubmissionConflict):
        store.save_comparison(
            turn.request_id, reference.snapshot.task_id, current.snapshot.task_id
        )


def test_reference_admission_checks_ownership_and_retry_identity(comparison_store):
    store, _, _ = comparison_store
    local_session = store.create_session()
    foreign_session = store.create_session()
    local_asset = save_original(store)
    foreign_asset = save_original(store)
    local = save_task(store, local_session.session_id, local_asset)
    foreign = save_task(store, foreign_session.session_id, foreign_asset)
    request_id = uuid4()
    accepted = TurnSubmission(
        request_id=request_id,
        session_id=local_session.session_id,
        text="compare",
        reference_task_id=local.snapshot.task_id,
    )

    assert store.admit_turn(accepted, {}).reference_task_id == local.snapshot.task_id
    with pytest.raises(SubmissionConflict):
        store.admit_turn(
            TurnSubmission.model_validate(
                accepted.model_dump(mode="json")
                | {"reference_task_id": str(foreign.snapshot.task_id)}
            ),
            {},
        )
    store.update_turn(
        request_id,
        status="completed",
        stage="finished",
        reply="done",
    )
    with pytest.raises(SubmissionConflict):
        store.admit_turn(
            TurnSubmission(
                request_id=uuid4(),
                session_id=local_session.session_id,
                text="compare",
                reference_task_id=foreign.snapshot.task_id,
            ),
            {},
        )
