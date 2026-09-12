from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pipe_inspector.config import Settings
from pipe_inspector.contracts.tasks import TaskSnapshot
from pipe_inspector.workflow.start import (
    InspectionStarter,
    InspectionStartError,
    run_config,
    video_run_config,
)
from temporalio.common import WorkflowIDReusePolicy


class MemoryStore:
    def __init__(self, kind="image"):
        self.task = SimpleNamespace(
            input_asset=object(),
            snapshot=TaskSnapshot(task_id=uuid4(), updated_at=datetime.now(timezone.utc)),
        )
        self.kind = kind
        self.run = None

    def get_task(self, task_id):
        return self.task

    def get_asset(self, ref):
        return SimpleNamespace(kind=self.kind)

    def get_run(self, task_id):
        return self.run

    def ensure_run(self, task_id, config):
        if self.run is None:
            self.run = {"task_id": str(task_id), "run_id": str(uuid4()), "config": config}
        return self.run

    def update_snapshot(self, snapshot):
        self.task.snapshot = snapshot
        return self.task


@pytest.mark.asyncio
async def test_start_pins_config_and_reuses_workflow_identity_after_ambiguous_failure():
    settings = Settings(_env_file=None, vlm_model="fixture-model")
    store = MemoryStore()
    client = SimpleNamespace(start_workflow=AsyncMock(side_effect=[OSError(), object()]))
    connect = AsyncMock(return_value=client)
    starter = InspectionStarter(settings, store, connect=connect)
    task_id = store.task.snapshot.task_id
    with pytest.raises(InspectionStartError) as error:
        await starter.start(task_id)
    assert error.value.code == "workflow_unavailable"
    original = store.run.copy()
    assert await starter.start(task_id) == store.task
    assert store.run == original
    assert client.start_workflow.call_count == 2
    first, second = client.start_workflow.call_args_list
    assert first == second
    assert first.kwargs["id"] == f"inspection:{task_id}"
    assert first.kwargs["id_reuse_policy"] == WorkflowIDReusePolicy.REJECT_DUPLICATE


@pytest.mark.asyncio
async def test_video_pins_video_policy_and_starts_video_workflow():
    client = SimpleNamespace(start_workflow=AsyncMock())
    connect = AsyncMock(return_value=client)
    store = MemoryStore("video")
    task_id = store.task.snapshot.task_id

    assert (
        await InspectionStarter(Settings(_env_file=None), store, connect=connect).start(task_id)
        == store.task
    )

    config = store.run["config"]
    assert config["operation"] == "video_inspection"
    assert config["video_policy_version"] == "video-review-v1"
    assert config["video_aggregation"]["policy_version"] == "video-events-v1"
    assert config["video_chunk_size"] == 8
    assert config["video_review_budget"] == 32
    assert store.task.snapshot.can_cancel is True
    assert client.start_workflow.call_args.args == ("VideoInspectionWorkflow", store.run)


@pytest.mark.parametrize("kind", ["image", "video"])
@pytest.mark.parametrize("terminal", [False, True])
async def test_explicit_retired_plan_is_rejected_even_when_persisted_but_resume_remains(
    kind, terminal
):
    from pipe_inspector.contracts.planning import PlanStep, TaskPlan

    settings = Settings(_env_file=None)
    store = MemoryStore(kind)
    task_id = store.task.snapshot.task_id
    plan = TaskPlan(
        open_anomaly_audit=True,
        steps=(
            PlanStep(step_id="detect", action="detect"),
            PlanStep(step_id="verify", action="verify", depends_on=("detect",)),
            PlanStep(step_id="open", action="open_scan", depends_on=("verify",)),
        ),
    )
    config = (video_run_config if kind == "video" else run_config)(settings) | {
        "plan": plan.model_dump(mode="json"),
        "open_scan": {"version": "open-scan-v1"},
    }
    saved_run = store.ensure_run(task_id, config)
    if terminal:
        store.task.snapshot = TaskSnapshot.model_validate(
            store.task.snapshot.model_dump() | {"runtime_status": "cancelled"}
        )
    client = SimpleNamespace(start_workflow=AsyncMock())
    connect = AsyncMock(return_value=client)
    starter = InspectionStarter(settings, store, connect=connect)
    with pytest.raises(InspectionStartError) as error:
        await starter.start(task_id, plan=plan)
    assert error.value.code == "tool_unavailable"
    assert error.value.status_code == 409
    connect.assert_not_awaited()
    assert store.run == saved_run
    assert await starter.start(task_id) == store.task
    assert store.run == saved_run
    if terminal:
        connect.assert_not_awaited()
    else:
        assert client.start_workflow.call_args.args[1] == saved_run


def test_video_configuration_extends_existing_safe_run_pins():
    detector = SimpleNamespace(
        detector_id="fixture-detector",
        model_version="fixture-v1",
        label_space_version="five-v1",
    )
    settings = Settings(_env_file=None, vlm_api_key="private-key", vlm_model="fixture")

    config = video_run_config(settings, detector=detector, force_review=True)

    assert config["detector_id"] == "fixture-detector"
    assert config["force_review"] is True
    assert config["operation"] == "video_inspection"
    assert "private-key" not in str(config)


def test_configuration_contains_versions_but_never_credentials():
    settings = Settings(_env_file=None, vlm_api_key="private-key", vlm_model="fixture")
    config = run_config(settings)
    assert config["detector_id"] == "ultralytics-yolo26"
    assert config["vlm_model"] == "fixture"
    assert "private-key" not in str(config)
    assert "api_key" not in str(config)
    assert config["policy_version"]
    assert config["human_review_version"] == "human-review-v1"


@pytest.mark.asyncio
async def test_waiting_image_can_cancel_without_restarting_models():
    store = MemoryStore()
    task_id = store.task.snapshot.task_id
    store.task.snapshot = TaskSnapshot.model_validate(
        store.task.snapshot.model_dump()
        | {
            "runtime_status": "waiting_review",
            "stage": "human_review",
            "waiting_reason": "需要人工确认",
            "can_cancel": True,
        }
    )
    store.run = {"task_id": str(task_id), "config": {"human_review_version": "human-review-v1"}}
    handle = SimpleNamespace(execute_update=AsyncMock(return_value=True))
    connect = AsyncMock(return_value=SimpleNamespace(get_workflow_handle=lambda _: handle))
    starter = InspectionStarter(Settings(_env_file=None), store, connect=connect)
    assert await starter.cancel(task_id) == store.task
    handle.execute_update.assert_awaited_once_with("request_cancel")


@pytest.mark.parametrize(
    "url",
    [
        "https://user:fixture-secret@example.invalid/v1",
        "https://example.invalid/v1?api_key=fixture-secret",
        "https://example.invalid/v1#fixture-secret",
    ],
)
def test_provider_url_cannot_persist_embedded_credentials(url):
    with pytest.raises(InspectionStartError) as error:
        run_config(Settings(_env_file=None, vlm_base_url=url))
    assert error.value.code == "invalid_vlm_configuration"
    assert "fixture-secret" not in str(error.value)


async def test_conversation_plan_and_explicit_review_are_pinned_across_retry():
    from pipe_inspector.agent.intent import compile_inspection_plan
    from pipe_inspector.contracts.conversation import ConversationIntent
    from pipe_inspector.contracts.planning import ActionKind

    plan = compile_inspection_plan(
        ConversationIntent(kind="inspect", actions=("verify",), message="检查并复检"),
        available_actions=set(ActionKind),
    )
    store = MemoryStore()
    client = SimpleNamespace(start_workflow=AsyncMock())
    starter = InspectionStarter(
        Settings(_env_file=None), store, connect=AsyncMock(return_value=client)
    )
    task_id = store.task.snapshot.task_id
    await starter.start(task_id, force_review=True, plan=plan)
    assert store.run["config"]["force_review"] is True
    assert store.run["config"]["plan"] == plan.model_dump(mode="json")
    await starter.start(task_id)
    assert client.start_workflow.call_args_list[0] == client.start_workflow.call_args_list[1]
    with pytest.raises(InspectionStartError) as error:
        await starter.start(task_id, force_review=False, plan=plan)
    assert error.value.code == "plan_conflict"
    # A manual run may finish before a delayed conversation start is reconciled.
    # Terminal state must not hide a mismatch in required semantic operations.
    store.task.snapshot = TaskSnapshot.model_validate(
        store.task.snapshot.model_dump() | {"runtime_status": "cancelled", "can_cancel": False}
    )
    with pytest.raises(InspectionStartError) as terminal_error:
        await starter.start(task_id, force_review=False, plan=plan)
    assert terminal_error.value.code == "plan_conflict"


@pytest.mark.asyncio
async def test_cancel_updates_only_started_nonterminal_video_tasks():
    store = MemoryStore("video")
    task_id = store.task.snapshot.task_id
    store.run = {"task_id": str(task_id), "run_id": str(uuid4()), "config": {}}
    handle = SimpleNamespace(execute_update=AsyncMock(return_value=True))
    client = SimpleNamespace(get_workflow_handle=lambda workflow_id: handle)
    connect = AsyncMock(return_value=client)
    starter = InspectionStarter(Settings(_env_file=None), store, connect=connect)

    assert await starter.cancel(task_id) == store.task
    handle.execute_update.assert_awaited_once_with("request_cancel")

    handle.execute_update.return_value = False
    with pytest.raises(InspectionStartError) as error:
        await starter.cancel(task_id)
    assert error.value.code == "finalizing"

    store.task.snapshot = TaskSnapshot.model_validate(
        store.task.snapshot.model_dump() | {"runtime_status": "cancelled", "can_cancel": False}
    )
    handle.execute_update.reset_mock()
    connect.reset_mock()
    assert await starter.cancel(task_id) == store.task
    handle.execute_update.assert_not_awaited()
    connect.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_update_timeout_is_safe_and_retryable():
    from temporalio.client import WorkflowUpdateRPCTimeoutOrCancelledError

    store = MemoryStore("video")
    task_id = store.task.snapshot.task_id
    store.run = {"task_id": str(task_id), "run_id": str(uuid4()), "config": {}}
    handle = SimpleNamespace(
        execute_update=AsyncMock(side_effect=WorkflowUpdateRPCTimeoutOrCancelledError())
    )
    client = SimpleNamespace(get_workflow_handle=lambda _: handle)
    with pytest.raises(InspectionStartError) as error:
        await InspectionStarter(
            Settings(_env_file=None), store, connect=AsyncMock(return_value=client)
        ).cancel(task_id)
    assert error.value.code == "workflow_unavailable"
    assert store.task.snapshot.runtime_status == "queued"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "has_run", "code"),
    [("image", True, "video_only"), ("video", False, "not_started")],
)
async def test_cancel_refuses_images_and_videos_without_a_run(kind, has_run, code):
    store = MemoryStore(kind)
    if has_run:
        store.run = {"task_id": str(uuid4()), "run_id": str(uuid4()), "config": {}}
    connect = AsyncMock()

    with pytest.raises(InspectionStartError) as error:
        await InspectionStarter(Settings(_env_file=None), store, connect=connect).cancel(
            store.task.snapshot.task_id
        )

    assert error.value.code == code
    assert error.value.status_code == 409
    connect.assert_not_awaited()
