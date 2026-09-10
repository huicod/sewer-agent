from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pipe_inspector.agent.api_client import APIClientError
from pipe_inspector.config import Settings
from pipe_inspector.contracts.assets import AssetRef
from pipe_inspector.contracts.detection import DetectionBatch
from pipe_inspector.contracts.inspection import ImageReview, QualitySummary
from pipe_inspector.contracts.tasks import TaskSnapshot
from pipe_inspector.workflow.activities import ImageActivities
from pipe_inspector.workflow.start import run_config
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment


class Store:
    def __init__(self):
        self.task_id = uuid4()
        self.task = SimpleNamespace(
            input_asset=AssetRef(asset_id=uuid4()),
            snapshot=TaskSnapshot(task_id=self.task_id, updated_at=datetime.now(timezone.utc)),
        )
        self.steps, self.calls = {}, []

    def get_task(self, task_id):
        return self.task

    def load_step(self, task_id, step):
        return self.steps.get(step)

    def save_step(self, task_id, step, payload, call=None):
        if step not in self.steps:
            self.steps[step] = payload
            if call:
                self.record_call(call)
        return self.steps[step]

    def record_call(self, call):
        self.calls = [
            c for c in self.calls if (c.call_id, c.attempt) != (call.call_id, call.attempt)
        ]
        self.calls.append(call)

    def list_calls(self, task_id):
        return tuple(self.calls)

    def update_snapshot(self, snapshot):
        self.task.snapshot = snapshot
        return self.task


@pytest.fixture
def setup(monkeypatch):
    store = Store()
    detector = SimpleNamespace(detector_id="test", model_version="v1", label_space_version="five")
    batch = DetectionBatch(
        detector_id="test",
        model_version="v1",
        label_space_version="five",
        width=16,
        height=16,
        detections=(),
    )
    detector.predict_image = Mock(return_value=batch)
    quality = QualitySummary(
        mean_luminance=100, contrast=50, sharpness=500, adequate=True, reasons=()
    )
    monkeypatch.setattr("pipe_inspector.workflow.activities.analyze_quality", lambda _: quality)
    settings = Settings(_env_file=None)
    run = {
        "task_id": str(store.task_id),
        "run_id": str(uuid4()),
        "config": run_config(settings, detector),
    }
    media = SimpleNamespace(path_for=lambda _: Path("fixture.png"))
    activities = ImageActivities(settings, store, media, detector=detector)
    return activities, store, run, detector


def test_detection_checkpoint_replay_does_not_repeat_gpu_call(setup):
    activities, store, run, detector = setup
    env = ActivityEnvironment()
    first = env.run(activities.detect_image, run)
    assert env.run(activities.detect_image, run) == first
    detector.predict_image.assert_called_once()
    assert len(store.calls) == 1
    assert store.calls[0].status == "succeeded"


@pytest.mark.asyncio
async def test_missing_vlm_config_finishes_inconclusive_not_normal(setup):
    activities, store, run, _ = setup
    env = ActivityEnvironment()
    env.run(activities.detect_image, run)
    with pytest.raises(ApplicationError) as error:
        await env.run(activities.verify_image, run)
    assert error.value.non_retryable
    assert error.value.type == "vlm_not_configured"
    result = env.run(activities.finish_image, {"run": run, "error": error.value.type})
    assert result["runtime_status"] == "partial"
    assert result["inspection_result"] == "inconclusive"
    assert store.calls[-1].error.code == "vlm_not_configured"


@pytest.mark.asyncio
async def test_full_negative_audit_can_finish_normal_and_replay_final_snapshot(setup):
    activities, store, run, _ = setup
    activities.reviewer = SimpleNamespace(
        review=AsyncMock(
            return_value=ImageReview(
                image_assessable=True,
                full_image_audited=True,
                candidates=(),
                additional_anomalies=(),
                summary="全图无可见缺陷",
            )
        )
    )
    env = ActivityEnvironment()
    env.run(activities.detect_image, run)
    await env.run(activities.verify_image, run)
    result = env.run(activities.finish_image, {"run": run})
    assert result["inspection_result"] == "normal"
    assert result["runtime_status"] == "completed"
    assert env.run(activities.finish_image, {"run": run}) == result


@pytest.mark.asyncio
async def test_transient_api_error_is_uncertain_and_retryable(setup):
    activities, store, run, _ = setup
    activities.reviewer = SimpleNamespace(
        review=AsyncMock(side_effect=APIClientError("timeout", retryable=True))
    )
    env = ActivityEnvironment()
    env.run(activities.detect_image, run)
    with pytest.raises(ApplicationError) as error:
        await env.run(activities.verify_image, run)
    assert not error.value.non_retryable
    assert store.calls[-1].status == "uncertain"
    assert store.calls[-1].error.code == "timeout"


def test_model_version_mismatch_stops_before_inference(setup):
    activities, _, run, detector = setup
    run["config"]["model_version"] = "different"
    with pytest.raises(ApplicationError) as error:
        ActivityEnvironment().run(activities.detect_image, run)
    assert error.value.type == "configuration_changed"
    detector.predict_image.assert_not_called()


@pytest.mark.parametrize("force_review", [False, True])
async def test_explicit_review_overrides_strong_detection_skip(setup, force_review):
    from pipe_inspector.contracts.detection import Detection

    activities, store, run, detector = setup
    detector.predict_image.return_value = DetectionBatch(
        detector_id="test",
        model_version="v1",
        label_space_version="five",
        width=16,
        height=16,
        detections=(
            Detection(label="AJ", native_label="AJ", score=0.95, bbox_xyxy=(1, 1, 10, 10)),
        ),
    )
    run["config"]["force_review"] = force_review
    activities.reviewer = SimpleNamespace(
        review=AsyncMock(side_effect=APIClientError("timeout", retryable=True))
    )
    env = ActivityEnvironment()
    env.run(activities.detect_image, run)
    if force_review:
        with pytest.raises(ApplicationError):
            await env.run(activities.verify_image, run)
        result = env.run(activities.finish_image, {"run": run, "error": "timeout"})
        assert result["runtime_status"] == "partial"
        assert result["inspection_result"] != "normal"
        activities.reviewer.review.assert_awaited_once()
    else:
        result = await env.run(activities.verify_image, run)
        assert result["skipped"] is True
        activities.reviewer.review.assert_not_awaited()
