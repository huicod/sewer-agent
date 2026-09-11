from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from pipe_inspector.agent.recall_policy import decide_image_recall
from pipe_inspector.config import Settings
from pipe_inspector.contracts.assets import AssetRef
from pipe_inspector.contracts.detection import Detection, DetectionBatch
from pipe_inspector.contracts.inspection import QualitySummary
from pipe_inspector.contracts.tasks import TaskSnapshot
from pipe_inspector.contracts.tools import ToolCall
from pipe_inspector.workflow.activities import ImageActivities
from pipe_inspector.workflow.image import ImageInspectionWorkflow
from pipe_inspector.workflow.start import run_config
from temporalio.testing import ActivityEnvironment


class FaultStore:
    def __init__(self) -> None:
        self.task_id = uuid4()
        self.asset = AssetRef(asset_id=uuid4())
        self.task = SimpleNamespace(
            input_asset=self.asset,
            snapshot=TaskSnapshot(task_id=self.task_id, updated_at=datetime.now(timezone.utc)),
        )
        self.steps: dict[str, dict] = {}
        self.calls: list[ToolCall] = []
        self.lose_detect_reply = False
        self.detect_reply_lost = False
        self.snapshot_failures = 0
        self.snapshot_updates = 0

    def get_task(self, task_id):
        return self.task

    def load_step(self, task_id, step):
        return self.steps.get(step)

    def save_step(self, task_id, step, payload, call=None):
        if step not in self.steps:
            self.steps[step] = payload
            if call is not None:
                self.record_call(call)
        if step == "detect" and self.lose_detect_reply and not self.detect_reply_lost:
            self.detect_reply_lost = True
            raise RuntimeError("injected completion acknowledgement loss")
        return self.steps[step]

    def record_call(self, call):
        self.calls = [
            stored
            for stored in self.calls
            if (stored.call_id, stored.attempt) != (call.call_id, call.attempt)
        ]
        self.calls.append(call)

    def list_calls(self, task_id):
        return tuple(self.calls)

    def update_snapshot(self, snapshot):
        self.snapshot_updates += 1
        if self.snapshot_failures and (snapshot.is_terminal or snapshot.stage == "human_review"):
            self.snapshot_failures -= 1
            raise RuntimeError("injected projection write failure")
        self.task.snapshot = snapshot
        return self.task


def make_case(monkeypatch, *, detections=()):
    store = FaultStore()
    detector = SimpleNamespace(detector_id="test", model_version="v1", label_space_version="five")
    detector.predict_image = Mock(
        return_value=DetectionBatch(
            detector_id="test",
            model_version="v1",
            label_space_version="five",
            width=32,
            height=24,
            detections=detections,
        )
    )
    quality = QualitySummary(
        mean_luminance=100,
        contrast=40,
        sharpness=200,
        adequate=True,
        reasons=(),
    )
    monkeypatch.setattr("pipe_inspector.workflow.activities.analyze_quality", lambda _: quality)
    settings = Settings(_env_file=None)
    run = {
        "task_id": str(store.task_id),
        "run_id": str(uuid4()),
        "config": run_config(settings, detector),
    }
    activities = ImageActivities(
        settings,
        store,
        SimpleNamespace(path_for=lambda _: Path("fixture.png")),
        detector=detector,
    )
    return activities, store, run, detector


@pytest.mark.asyncio
async def test_workflow_uses_bounded_detect_and_durable_finalization_retries(monkeypatch) -> None:
    policies = {}

    async def execute_activity(name, payload, **options):
        policies[name] = options["retry_policy"]
        if name == "finish_image":
            return {"runtime_status": "completed"}
        return None

    monkeypatch.setattr("pipe_inspector.workflow.image.workflow.execute_activity", execute_activity)
    run = {"config": {"gpu_queue": "test-gpu", "api_timeout_seconds": 2}}

    result = await ImageInspectionWorkflow().run(run)

    assert result == {"runtime_status": "completed"}
    assert policies["detect_image"].maximum_attempts == 3
    finish_policy = policies["finish_image"]
    assert finish_policy.maximum_attempts == 0
    assert finish_policy.backoff_coefficient == 2
    assert finish_policy.maximum_interval == timedelta(seconds=30)


def test_detect_checkpoint_survives_lost_first_activity_response(monkeypatch) -> None:
    activities, store, run, detector = make_case(monkeypatch)
    store.lose_detect_reply = True
    env = ActivityEnvironment()

    with pytest.raises(RuntimeError, match="acknowledgement loss"):
        env.run(activities.detect_image, run)
    replayed = env.run(activities.detect_image, run)

    assert replayed == store.steps["detect"]
    detector.predict_image.assert_called_once()
    assert [(call.attempt, str(call.status)) for call in store.calls] == [(1, "succeeded")]


def test_cached_final_checkpoint_repairs_projection_without_redeciding(monkeypatch) -> None:
    activities, store, run, _ = make_case(monkeypatch)
    env = ActivityEnvironment()
    env.run(activities.detect_image, run)
    store.snapshot_failures = 1
    policy = Mock(side_effect=decide_image_recall)
    monkeypatch.setattr("pipe_inspector.workflow.activities.decide_image_recall", policy)

    with pytest.raises(RuntimeError, match="projection write failure"):
        env.run(activities.finish_image, {"run": run, "error": "vlm_not_configured"})
    saved = store.steps["final"]
    replayed = env.run(activities.finish_image, {"run": run, "error": "vlm_not_configured"})

    assert replayed == saved
    assert store.task.snapshot.model_dump(mode="json") == saved
    assert policy.call_count == 1


def test_finish_configuration_drift_preserves_candidates_without_running_changed_policy(
    monkeypatch,
) -> None:
    candidate = Detection(
        label="AJ",
        native_label="AJ",
        score=0.93,
        bbox_xyxy=(2, 3, 18, 20),
    )
    activities, store, run, _ = make_case(monkeypatch, detections=(candidate,))
    env = ActivityEnvironment()
    env.run(activities.detect_image, run)
    run["config"]["prompt_version"] = "stale-prompt"
    changed_policy = Mock(side_effect=AssertionError("changed policy must not run"))
    monkeypatch.setattr("pipe_inspector.workflow.activities.decide_image_recall", changed_policy)

    result = env.run(activities.finish_image, {"run": run})

    assert result["runtime_status"] == "failed"
    assert result["inspection_result"] == "inconclusive"
    assert "configuration_changed" in result["waiting_reason"]
    assert len(result["findings"]) == 1
    assert result["findings"][0]["canonical_label"] == "AJ"
    assert result["findings"][0]["review_status"] == "needs_review"
    changed_policy.assert_not_called()


def test_finish_waiting_reason_keeps_specific_review_error(monkeypatch) -> None:
    activities, _, run, _ = make_case(monkeypatch)
    env = ActivityEnvironment()
    env.run(activities.detect_image, run)

    result = env.run(activities.finish_image, {"run": run, "error": "vlm_not_configured"})

    assert "review_failed" in result["waiting_reason"]
    assert "vlm_not_configured" in result["waiting_reason"]


def test_finish_marks_interrupted_running_call_uncertain_without_changing_attempt(
    monkeypatch,
) -> None:
    activities, store, run, _ = make_case(monkeypatch)
    running = ToolCall(
        call_id=run["config"]["verify_call_id"],
        task_id=store.task_id,
        step_id="verify",
        idempotency_key=f"{run['run_id']}:verify",
        attempt=2,
        tool_name="verify",
        tool_version="1",
        model_version=run["config"]["vlm_model"],
        resource="api",
        timeout_seconds=run["config"]["api_timeout_seconds"],
        input_assets=(store.asset,),
        status="running",
    )
    store.record_call(running)

    ActivityEnvironment().run(
        activities.finish_image,
        {"run": run, "error": "activity_interrupted"},
    )

    assert len(store.calls) == 1
    assert store.calls[0].attempt == 2
    assert str(store.calls[0].status) == "uncertain"
    assert store.calls[0].error.code == "activity_interrupted"
