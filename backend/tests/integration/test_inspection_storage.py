"""Opt-in durable inspection storage tests using a disposable PostgreSQL schema."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from uuid import UUID, uuid4

import psycopg
import pytest
from pipe_inspector.config import Settings
from pipe_inspector.contracts.assets import AssetKind, AssetRecord, AssetRef
from pipe_inspector.contracts.intake import TaskSubmission
from pipe_inspector.contracts.tasks import TaskSnapshot
from pipe_inspector.contracts.tools import ToolCall
from pipe_inspector.storage.inspection import InspectionRepository
from pipe_inspector.storage.repository import MissingRecord, SubmissionConflict
from psycopg import sql

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("SEWER_RUN_STORAGE_TEST") != "1",
        reason="explicit opt-in required: uses the local PostgreSQL application database",
    ),
]


@pytest.fixture
def inspection_store():
    settings = Settings()
    schema = "test_inspection_storage_" + uuid4().hex
    with psycopg.connect(settings.database_dsn.get_secret_value()) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        store = InspectionRepository(settings.database_dsn, schema=schema)
        store.initialize()
        store.initialize()
        yield store, settings, schema
    finally:
        with psycopg.connect(settings.database_dsn.get_secret_value()) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )


def create_task(store: InspectionRepository):
    session = store.create_session()
    asset = AssetRecord(
        ref=AssetRef(asset_id=uuid4()),
        kind=AssetKind.IMAGE,
        media_type="image/png",
        byte_size=128,
        width=16,
        height=12,
    )
    store.save_asset(asset)
    task = store.create_task(
        TaskSubmission(
            request_id=uuid4(),
            session_id=session.session_id,
            input_asset=asset.ref,
            instruction="检查图像并保留原始提交",
        )
    )
    return task, asset


def tool_call(task_id, asset, *, call_id, attempt, status, error=None):
    return ToolCall(
        call_id=call_id,
        task_id=task_id,
        step_id="detect",
        idempotency_key=f"detect:{task_id}",
        attempt=attempt,
        tool_name="detect",
        tool_version="detector-adapter-v1",
        model_version="sewer-five-class-v1",
        resource="gpu",
        timeout_seconds=30,
        input_assets=(asset.ref,),
        status=status,
        error=error,
    )


def test_run_config_is_first_write_immutable_and_shared_by_concurrent_retries(
    inspection_store,
):
    store, settings, schema = inspection_store
    task, _ = create_task(store)
    config = {
        "detector_id": "yolo26-sewer",
        "model_version": "sewer-five-class-v1",
        "label_space": ["AJ", "BX", "FS", "PL", "ZW"],
        "policy_version": "image-policy-v1",
        "prompt_version": "image-review-v1",
        "api_timeout_seconds": 60,
        "max_image_bytes": 8_388_608,
    }
    workers = 4
    barrier = Barrier(workers)

    def ensure_once(_):
        reopened = InspectionRepository(settings.database_dsn, schema=schema)
        barrier.wait(timeout=10)
        return reopened.ensure_run(task.snapshot.task_id, config)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        runs = list(pool.map(ensure_once, range(workers)))

    assert runs == [runs[0]] * workers
    assert set(runs[0]) == {"run_id", "task_id", "config"}
    assert isinstance(runs[0]["run_id"], str)
    assert UUID(runs[0]["run_id"]).version == 4
    assert runs[0]["task_id"] == str(task.snapshot.task_id)
    assert runs[0]["config"] == config
    assert InspectionRepository(settings.database_dsn, schema=schema).get_run(
        task.snapshot.task_id
    ) == runs[0]
    with pytest.raises(SubmissionConflict, match="^submission conflicts with stored state$"):
        store.ensure_run(task.snapshot.task_id, config | {"model_version": "changed"})
    with pytest.raises(MissingRecord, match="^record not found$"):
        store.ensure_run(uuid4(), config)


def test_skipped_call_commits_with_its_checkpoint(inspection_store):
    store, _, _ = inspection_store
    task, asset = create_task(store)
    call = ToolCall(
        call_id=uuid4(),
        task_id=task.snapshot.task_id,
        step_id="verify",
        idempotency_key=f"verify:{task.snapshot.task_id}",
        tool_name="verify",
        tool_version="image-review-v1",
        model_version="review-model-v1",
        resource="api",
        timeout_seconds=30,
        input_assets=(asset.ref,),
        status="skipped",
        skip_reason="review not required",
    )

    payload = store.save_step(
        task.snapshot.task_id, "verify", {"review": None, "skipped": True}, call
    )

    assert payload == {"review": None, "skipped": True}
    assert store.list_calls(task.snapshot.task_id) == (call,)


def test_call_attempts_are_upserted_independently_and_checkpoints_are_cached(
    inspection_store,
):
    store, _, _ = inspection_store
    task, asset = create_task(store)
    task_id = task.snapshot.task_id
    call_id = uuid4()
    first = tool_call(
        task_id,
        asset,
        call_id=call_id,
        attempt=1,
        status="uncertain",
        error={"code": "timeout", "retryable": True, "message": "detector timed out"},
    )
    second = tool_call(
        task_id, asset, call_id=call_id, attempt=2, status="running"
    )
    succeeded = tool_call(
        task_id, asset, call_id=call_id, attempt=2, status="succeeded"
    )

    store.record_call(first)
    store.record_call(second)
    stored = store.save_step(task_id, "detect", {"detections": ["AJ"]}, succeeded)
    replayed = store.save_step(task_id, "detect", {"detections": ["BX"]})

    assert stored == {"detections": ["AJ"]}
    assert replayed == stored
    assert store.load_step(task_id, "detect") == stored
    calls = store.list_calls(task_id)
    assert [(call.attempt, str(call.status)) for call in calls] == [
        (1, "uncertain"),
        (2, "succeeded"),
    ]
    assert calls[0].error.code == "timeout"


def test_checkpoint_and_success_call_commit_atomically(inspection_store):
    store, _, _ = inspection_store
    task, asset = create_task(store)
    task_id = task.snapshot.task_id
    succeeded = tool_call(
        task_id, asset, call_id=uuid4(), attempt=1, status="succeeded"
    )

    with pytest.raises(TypeError):
        store.save_step(task_id, "detect", {"not_json": {1, 2}}, succeeded)

    assert store.load_step(task_id, "detect") is None
    assert store.list_calls(task_id) == ()


def test_snapshot_update_preserves_submission_and_terminal_projection(inspection_store):
    store, _, _ = inspection_store
    task, _ = create_task(store)
    task_id = task.snapshot.task_id
    running_time = datetime.now(timezone.utc)
    running = TaskSnapshot(
        task_id=task_id,
        runtime_status="running",
        stage="detecting",
        stage_progress=0.5,
        overall_progress=0.25,
        processed_frames=1,
        updated_at=running_time,
    )

    updated = store.update_snapshot(running)

    assert updated.request_id == task.request_id
    assert updated.session_id == task.session_id
    assert updated.input_asset == task.input_asset
    assert updated.instruction == "检查图像并保留原始提交"
    assert updated.created_at == task.created_at
    assert updated.snapshot == running

    completed = TaskSnapshot(
        task_id=task_id,
        runtime_status="completed",
        inspection_result="normal",
        stage="finished",
        stage_progress=1,
        overall_progress=1,
        processed_frames=1,
        updated_at=running_time + timedelta(seconds=1),
        coverage={
            "sufficient_coverage": True,
            "sufficient_quality": True,
            "negative_audit_complete": True,
        },
    )
    terminal = store.update_snapshot(completed)
    regressed = TaskSnapshot(
        task_id=task_id,
        runtime_status="running",
        stage="verifying",
        stage_progress=0,
        overall_progress=0.75,
        processed_frames=1,
        updated_at=running_time + timedelta(seconds=2),
    )

    with pytest.raises(SubmissionConflict, match="^submission conflicts with stored state$"):
        store.update_snapshot(regressed)

    assert store.get_task(task_id) == terminal
    assert terminal.snapshot == completed


def test_storage_methods_reject_cross_task_call_and_missing_task(inspection_store):
    store, _, _ = inspection_store
    task, asset = create_task(store)
    other, _ = create_task(store)
    mismatched = tool_call(
        other.snapshot.task_id,
        asset,
        call_id=uuid4(),
        attempt=1,
        status="succeeded",
    )

    with pytest.raises(ValueError, match="call must belong to the checkpoint task and step"):
        store.save_step(task.snapshot.task_id, "detect", {"detections": []}, mismatched)
    with pytest.raises(MissingRecord, match="^record not found$"):
        store.update_snapshot(TaskSnapshot(task_id=uuid4(), updated_at=datetime.now(timezone.utc)))


async def test_reports_read_persisted_terminal_result_after_repository_reopen(
    inspection_store, tmp_path
):
    from httpx import ASGITransport, AsyncClient
    from pipe_inspector.api.app import create_app

    store, settings, schema = inspection_store
    task, _ = create_task(store)
    snapshot = TaskSnapshot(
        task_id=task.snapshot.task_id,
        updated_at=datetime.now(timezone.utc),
        runtime_status="partial",
        inspection_result="inconclusive",
        waiting_reason="复检暂未完成",
    )
    store.update_snapshot(snapshot)
    reopened = InspectionRepository(settings.database_dsn, schema=schema)
    app = create_app(Settings(_env_file=None, asset_root=tmp_path), repository=reopened)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        exported = await client.get(f"/api/tasks/{snapshot.task_id}/report?format=json")
        page = await client.get(f"/api/tasks/{snapshot.task_id}/report?format=html")
    assert exported.status_code == page.status_code == 200
    assert exported.json()["task"]["snapshot"] == snapshot.model_dump(mode="json")
    assert "复检暂未完成" in page.text
    assert reopened.get_task(snapshot.task_id).snapshot == snapshot
