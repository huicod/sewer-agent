"""Opt-in PostgreSQL tests for atomic linked image re-review admission."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest
from pipe_inspector.config import Settings
from pipe_inspector.contracts.assets import AssetKind, AssetRecord, AssetRef
from pipe_inspector.contracts.intake import TaskSubmission
from pipe_inspector.contracts.tasks import TaskSnapshot
from pipe_inspector.storage.repository import MissingRecord, SubmissionConflict
from pipe_inspector.storage.supplements import SupplementNotAllowed, SupplementRepository
from psycopg import sql
from psycopg.types.json import Jsonb

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("SEWER_RUN_STORAGE_TEST") != "1",
        reason="explicit opt-in required: uses the local PostgreSQL application database",
    ),
]

PARENT_RUN_CONFIG = {
    "detector_id": "fixture-detector",
    "model_version": "fixture-v1",
    "label_space_version": "five-v1",
    "policy_version": "image-policy-v1",
}


@pytest.fixture
def supplement_store():
    settings = Settings()
    schema = "test_supplement_storage_" + uuid4().hex
    with psycopg.connect(settings.database_dsn.get_secret_value()) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        store = SupplementRepository(settings.database_dsn, schema=schema)
        store.initialize()
        store.initialize()
        yield store, settings, schema
    finally:
        with psycopg.connect(settings.database_dsn.get_secret_value()) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def save_asset(store, *, kind=AssetKind.IMAGE):
    asset = AssetRecord(
        ref=AssetRef(asset_id=uuid4()),
        kind=kind,
        media_type="video/mp4" if kind == AssetKind.VIDEO else "image/png",
        byte_size=128,
        width=32,
        height=24,
        duration_seconds=1.0 if kind == AssetKind.VIDEO else None,
    )
    store.save_asset(asset)
    return asset


def detection_payload(**metadata):
    detection = {
        "detector_id": "fixture-detector",
        "model_version": "fixture-v1",
        "label_space_version": "five-v1",
        "width": 32,
        "height": 24,
        "detections": [],
    } | metadata
    return {
        "detection": detection,
        "quality": {
            "mean_luminance": 100,
            "contrast": 20,
            "sharpness": 50,
            "adequate": True,
        },
    }


def make_parent(
    store,
    *,
    session_id=None,
    asset=None,
    status="completed",
    save_run=True,
    save_detection=True,
    detection=None,
):
    session_id = session_id or store.create_session().session_id
    asset = asset or save_asset(store)
    task = store.create_task(
        TaskSubmission(
            request_id=uuid4(),
            session_id=session_id,
            input_asset=asset.ref,
            instruction="original inspection",
        )
    )
    if save_run:
        store.ensure_run(task.snapshot.task_id, PARENT_RUN_CONFIG)
    if save_detection:
        store.save_step(task.snapshot.task_id, "detect", detection or detection_payload())
    terminal = TaskSnapshot.model_validate(
        task.snapshot.model_dump()
        | {
            "runtime_status": status,
            "stage": "finished" if status in {"completed", "partial", "cancelled"} else "queued",
            "inspection_result": "normal" if status == "completed" else "inconclusive",
            "coverage": {
                "sufficient_coverage": status == "completed",
                "sufficient_quality": status == "completed",
                "negative_audit_complete": status == "completed",
            },
            "updated_at": datetime.now(timezone.utc),
        }
    )
    return store.update_snapshot(terminal), asset


def child_submission(parent, **changes):
    return TaskSubmission.model_validate(
        {
            "request_id": uuid4(),
            "session_id": parent.session_id,
            "input_asset": parent.input_asset.model_dump(mode="json"),
            "instruction": "re-review the saved image",
        }
        | changes
    )


def child_config(parent, **changes):
    return (
        PARENT_RUN_CONFIG
        | {
            "operation": "supplement_review",
            "parent_task_id": str(parent.snapshot.task_id),
            "force_review": True,
            "supplement_policy_version": "supplement-policy-v2",
            "verify_call_id": str(uuid4()),
        }
        | changes
    )


def test_admission_is_atomic_reopens_and_replay_keeps_first_pins(supplement_store):
    store, settings, schema = supplement_store
    parent, _ = make_parent(store)
    submission = child_submission(parent)
    config = child_config(parent)
    parent_verify = {"review": None, "skipped": True}
    store.save_step(parent.snapshot.task_id, "verify", parent_verify)

    child = store.admit_supplement(parent.snapshot.task_id, submission, config)
    replayed = SupplementRepository(settings.database_dsn, schema=schema).admit_supplement(
        parent.snapshot.task_id,
        submission,
        config | {"verify_call_id": str(uuid4()), "new_caller_pin": True},
    )

    assert replayed == child
    assert child.parent_task_id == parent.snapshot.task_id
    assert child.snapshot.runtime_status == "queued"
    assert store.get_task(child.snapshot.task_id) == child
    assert store.get_session(parent.session_id).tasks == (parent, child)
    assert store.get_run(child.snapshot.task_id)["config"] == config
    assert store.load_step(child.snapshot.task_id, "detect") == detection_payload()
    assert store.load_step(child.snapshot.task_id, "source") == {
        "task": parent.model_dump(mode="json"),
        "verify": parent_verify,
    }
    assert store.load_step(child.snapshot.task_id, "verify") is None
    assert store.load_step(child.snapshot.task_id, "final") is None
    assert store.list_calls(child.snapshot.task_id) == ()


def test_initialize_backfills_legacy_supplement_lineage(supplement_store):
    store, settings, schema = supplement_store
    parent, _ = make_parent(store)
    submission = child_submission(parent)
    config = child_config(parent)
    child = store.admit_supplement(parent.snapshot.task_id, submission, config)
    with store._connect() as connection:
        connection.execute(
            sql.SQL("DELETE FROM {} WHERE task_id = %s").format(
                store._table("sewer_image_operations")
            ),
            (child.snapshot.task_id,),
        )
        connection.execute(
            sql.SQL("INSERT INTO {} (parent_task_id, task_id) VALUES (%s, %s)").format(
                store._table("sewer_image_supplements")
            ),
            (parent.snapshot.task_id, child.snapshot.task_id),
        )

    reopened = SupplementRepository(settings.database_dsn, schema=schema)
    reopened.initialize()

    assert reopened.admit_supplement(parent.snapshot.task_id, submission, config) == child


def test_same_request_id_with_changed_submission_or_parent_conflicts(supplement_store):
    store, _, _ = supplement_store
    parent, _ = make_parent(store)
    other, _ = make_parent(store)
    submission = child_submission(parent)
    store.admit_supplement(parent.snapshot.task_id, submission, child_config(parent))

    changed = TaskSubmission.model_validate(submission.model_dump() | {"instruction": "different"})
    with pytest.raises(SubmissionConflict):
        store.admit_supplement(parent.snapshot.task_id, changed, child_config(parent))
    with pytest.raises(SubmissionConflict):
        store.admit_supplement(other.snapshot.task_id, submission, child_config(other))


def test_competing_requests_create_independent_children(supplement_store):
    store, settings, schema = supplement_store
    parent, _ = make_parent(store)
    workers = 2
    barrier = Barrier(workers)

    def admit(index):
        reopened = SupplementRepository(settings.database_dsn, schema=schema)
        submission = child_submission(parent, instruction=f"review {index}")
        barrier.wait(timeout=10)
        return reopened.admit_supplement(
            parent.snapshot.task_id, submission, child_config(parent)
        )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(admit, range(workers)))

    assert len({result.snapshot.task_id for result in results}) == 2
    children = [task for task in store.get_session(parent.session_id).tasks if task.parent_task_id]
    assert len(children) == 2


def test_concurrent_identical_request_returns_the_same_first_pinned_child(
    supplement_store,
):
    store, settings, schema = supplement_store
    parent, _ = make_parent(store)
    submission = child_submission(parent)
    barrier = Barrier(2)

    def admit(index):
        reopened = SupplementRepository(settings.database_dsn, schema=schema)
        barrier.wait(timeout=10)
        return reopened.admit_supplement(
            parent.snapshot.task_id,
            submission,
            child_config(parent, caller_pin=index),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        children = list(pool.map(admit, range(2)))

    assert children == [children[0], children[0]]
    stored_config = store.get_run(children[0].snapshot.task_id)["config"]
    assert stored_config["caller_pin"] in {0, 1}
    assert len(store.get_session(parent.session_id).tasks) == 2


def test_failed_child_does_not_prevent_another_frozen_child(supplement_store):
    store, _, _ = supplement_store
    parent, _ = make_parent(store)
    child = store.admit_supplement(
        parent.snapshot.task_id, child_submission(parent), child_config(parent)
    )
    failed = TaskSnapshot.model_validate(
        child.snapshot.model_dump()
        | {
            "runtime_status": "failed",
            "inspection_result": "inconclusive",
            "stage": "finished",
            "updated_at": datetime.now(timezone.utc),
        }
    )
    store.update_snapshot(failed)

    next_child = store.admit_supplement(
        parent.snapshot.task_id, child_submission(parent), child_config(parent)
    )

    assert next_child.snapshot.task_id != child.snapshot.task_id
    assert next_child.parent_task_id == parent.snapshot.task_id
    assert store.load_step(next_child.snapshot.task_id, "detect") == detection_payload()


def test_child_cannot_be_used_as_a_new_evidence_parent(supplement_store):
    store, _, _ = supplement_store
    parent, _ = make_parent(store)
    child = store.admit_supplement(
        parent.snapshot.task_id, child_submission(parent), child_config(parent)
    )

    with pytest.raises(SupplementNotAllowed, match="supplement_source_invalid") as nested:
        store.admit_supplement(child.snapshot.task_id, child_submission(child), child_config(child))

    assert nested.value.code == "supplement_source_invalid"


@pytest.mark.parametrize("status", ["queued", "running", "failed", "cancelled"])
def test_non_ready_parent_is_rejected(supplement_store, status):
    store, _, _ = supplement_store
    parent, _ = make_parent(store, status=status)

    with pytest.raises(SupplementNotAllowed, match="source_not_ready") as raised:
        store.admit_supplement(
            parent.snapshot.task_id, child_submission(parent), child_config(parent)
        )
    assert raised.value.code == "source_not_ready"


def test_missing_parent_video_and_reference_mismatches_are_rejected(supplement_store):
    store, _, _ = supplement_store
    parent, _ = make_parent(store)
    video_parent, _ = make_parent(store, asset=save_asset(store, kind=AssetKind.VIDEO))

    with pytest.raises(MissingRecord):
        store.admit_supplement(uuid4(), child_submission(parent), child_config(parent))
    with pytest.raises(SupplementNotAllowed, match="image_only"):
        store.admit_supplement(
            video_parent.snapshot.task_id,
            child_submission(video_parent),
            child_config(video_parent),
        )
    foreign_session = store.create_session()
    foreign_asset = save_asset(store)
    bad_references = [
        {"session_id": foreign_session.session_id},
        {"input_asset": foreign_asset.ref.model_dump(mode="json")},
    ]
    for changes in bad_references:
        with pytest.raises(SubmissionConflict):
            store.admit_supplement(
                parent.snapshot.task_id,
                child_submission(parent, **changes),
                child_config(parent),
            )


def test_derived_image_parent_is_rejected(supplement_store):
    store, _, _ = supplement_store
    parent, asset = make_parent(store)
    source = save_asset(store)
    derived = AssetRecord(
        ref=asset.ref,
        kind=AssetKind.ENHANCED_IMAGE,
        media_type="image/png",
        byte_size=asset.byte_size,
        width=asset.width,
        height=asset.height,
        parent=source.ref,
    )
    with store._connect() as connection:
        connection.execute(
            sql.SQL(
                """
                UPDATE {} SET parent_asset_id = %s, parent_version = %s, record = %s
                WHERE asset_id = %s AND version = %s
                """
            ).format(store._table("sewer_assets")),
            (
                source.ref.asset_id,
                source.ref.version,
                Jsonb(derived.model_dump(mode="json")),
                asset.ref.asset_id,
                asset.ref.version,
            ),
        )

    with pytest.raises(SupplementNotAllowed, match="image_only"):
        store.admit_supplement(
            parent.snapshot.task_id, child_submission(parent), child_config(parent)
        )


@pytest.mark.parametrize("missing", ["run", "detect"])
def test_missing_source_evidence_is_rejected(supplement_store, missing):
    store, _, _ = supplement_store
    parent, _ = make_parent(store, save_run=missing != "run", save_detection=missing != "detect")

    with pytest.raises(SupplementNotAllowed, match="source_evidence_missing") as raised:
        store.admit_supplement(
            parent.snapshot.task_id, child_submission(parent), child_config(parent)
        )
    assert raised.value.code == "source_evidence_missing"


def test_invalid_or_unpinned_source_evidence_is_rejected(supplement_store):
    store, _, _ = supplement_store
    invalid, _ = make_parent(store, detection={"bad": "payload"})
    mismatched, _ = make_parent(store, detection=detection_payload(detector_id="different"))

    for parent in (invalid, mismatched):
        with pytest.raises(SupplementNotAllowed, match="source_evidence_missing"):
            store.admit_supplement(
                parent.snapshot.task_id, child_submission(parent), child_config(parent)
            )


@pytest.mark.parametrize(
    "change",
    [
        {"operation": "inspect"},
        {"parent_task_id": str(uuid4())},
        {"force_review": False},
        {"supplement_policy_version": "changed"},
        {"detector_id": "different"},
        {"model_version": "different"},
        {"label_space_version": "different"},
    ],
)
def test_invalid_child_config_conflicts(supplement_store, change):
    store, _, _ = supplement_store
    parent, _ = make_parent(store)

    with pytest.raises(SubmissionConflict):
        store.admit_supplement(
            parent.snapshot.task_id,
            child_submission(parent),
            child_config(parent, **change),
        )


def test_late_serialization_failure_rolls_back_all_child_rows(supplement_store):
    store, _, _ = supplement_store
    parent, _ = make_parent(store)
    submission = child_submission(parent)

    with pytest.raises(TypeError):
        store.admit_supplement(
            parent.snapshot.task_id,
            submission,
            child_config(parent, unserializable={1, 2}),
        )

    with store._connect() as connection:
        task_count = connection.execute(
            sql.SQL("SELECT count(*) FROM {} WHERE request_id = %s").format(
                store._table("sewer_tasks")
            ),
            (submission.request_id,),
        ).fetchone()[0]
        relation_count = connection.execute(
            sql.SQL("SELECT count(*) FROM {}").format(store._table("sewer_image_operations"))
        ).fetchone()[0]
    assert task_count == relation_count == 0
    assert store.get_session(parent.session_id).tasks == (parent,)


def test_parent_link_survives_running_to_terminal_updates(supplement_store):
    store, _, _ = supplement_store
    parent, _ = make_parent(store, status="partial")
    child = store.admit_supplement(
        parent.snapshot.task_id, child_submission(parent), child_config(parent)
    )
    running = TaskSnapshot.model_validate(
        child.snapshot.model_dump()
        | {
            "runtime_status": "running",
            "stage": "verifying",
            "updated_at": datetime.now(timezone.utc),
        }
    )
    updated = store.update_snapshot(running)
    terminal = TaskSnapshot.model_validate(
        running.model_dump()
        | {
            "runtime_status": "partial",
            "inspection_result": "inconclusive",
            "stage": "finished",
            "updated_at": datetime.now(timezone.utc),
        }
    )

    assert updated.parent_task_id == parent.snapshot.task_id
    assert store.update_snapshot(terminal).parent_task_id == parent.snapshot.task_id
