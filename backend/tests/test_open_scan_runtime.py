from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from pipe_inspector.config import Settings
from pipe_inspector.contracts.assets import AssetRef
from pipe_inspector.contracts.detection import Detection, DetectionBatch
from pipe_inspector.contracts.inspection import ImageObservation, ImageReview, QualitySummary
from pipe_inspector.contracts.tasks import TaskSnapshot
from pipe_inspector.workflow.open_scan_activities import OpenScanActivities, apply_open_results
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

    def load_step(self, task_id, key):
        return self.steps.get(key)

    def save_step(self, task_id, key, value, call=None):
        if call is not None:
            assert call.task_id == task_id and call.step_id == key
        if key not in self.steps:
            self.steps[key] = value
            if call:
                self.record_call(call)
        return self.steps[key]

    def record_call(self, call):
        self.calls = [
            c for c in self.calls if (c.call_id, c.attempt) != (call.call_id, call.attempt)
        ]
        self.calls.append(call)

    def list_calls(self, task_id):
        return tuple(self.calls)

    def update_snapshot(self, snapshot):
        self.task.snapshot = snapshot


@pytest.fixture
def case():
    from pipe_inspector.contracts.open_scan import OpenCandidate, OpenDetectionBatch

    repo = Store()
    settings = Settings(
        _env_file=None,
        open_detector_path=Path("models/open.pt"),
        open_text_encoder_path=Path("models/encoder.ts"),
    )
    detector = SimpleNamespace(
        detector_id="primary", model_version="v1", label_space_version="five", unload=Mock()
    )
    observation = ImageObservation(
        detection=DetectionBatch(
            detector_id="primary",
            model_version="v1",
            label_space_version="five",
            width=32,
            height=32,
            detections=(),
        ),
        quality=QualitySummary(mean_luminance=100, contrast=50, sharpness=500, adequate=True),
    )
    repo.steps["detect"] = observation.model_dump(mode="json")
    repo.steps["verify"] = {
        "review": ImageReview(
            image_assessable=True,
            full_image_audited=True,
            candidates=(),
            additional_anomalies=(),
            summary="clear",
        ).model_dump(mode="json")
    }
    batch = OpenDetectionBatch(
        detector_id="yoloe",
        model_version="open.pt",
        vocabulary_version="sewer-open-v1",
        width=32,
        height=32,
        candidates=(OpenCandidate(label="PL", name="crack", score=0.7, bbox_xyxy=(1, 1, 20, 20)),),
    )
    adapter = SimpleNamespace(
        detector_id="yoloe",
        model_version="open.pt",
        vocabulary_version="sewer-open-v1",
        predict_image=Mock(return_value=batch),
        unload=Mock(),
    )
    run = {
        "task_id": str(repo.task_id),
        "run_id": str(uuid4()),
        "config": run_config(settings, detector),
    }
    activities = OpenScanActivities(
        settings,
        repo,
        SimpleNamespace(path_for=lambda _: Path("fixture.png")),
        detector=detector,
        open_detector=adapter,
    )
    return activities, repo, run, adapter, detector, observation


def test_new_config_opt_in_and_legacy_no_pins():
    assert "open_scan" not in run_config(Settings(_env_file=None))
    with pytest.raises(ValueError):
        Settings(_env_file=None, open_detector_path="open.pt")


def test_negative_scan_checkpoint_reused_and_resource_order(case):
    acts, repo, run, adapter, detector, _ = case
    env = ActivityEnvironment()
    order = []
    detector.unload.side_effect = lambda: order.append("primary_unload")
    adapter.predict_image.side_effect = lambda _: (
        order.append("open_predict") or adapter.predict_image.return_value
    )
    adapter.unload.side_effect = lambda: order.append("open_unload")
    plan = env.run(acts.select_open_scan, run)
    assert len(plan["selected"]) == 1
    assert plan["selected"][0]["reason"] == "negative_audit"
    payload = {"run": run, "index": 0}
    result = env.run(acts.scan_open_candidate, payload)
    assert env.run(acts.scan_open_candidate, payload) == result
    assert order == ["primary_unload", "open_predict", "open_unload"]
    assert repo.calls[-1].tool_name == "open_scan"
    assert repo.calls[-1].status == "succeeded"
    assert repo.task.snapshot.stage == "tools"


def test_confident_primary_no_suspicion_skips(case):
    acts, repo, run, adapter, _, observation = case
    detection = observation.detection.model_copy(
        update={
            "detections": (
                Detection(label="BX", native_label="BX", score=0.99, bbox_xyxy=(1, 1, 20, 20)),
            )
        }
    )
    repo.steps["detect"] = observation.model_copy(update={"detection": detection}).model_dump(
        mode="json"
    )
    assert ActivityEnvironment().run(acts.select_open_scan, run)["selected"] == []
    adapter.predict_image.assert_not_called()
    assert repo.calls[-1].status == "skipped"
    assert repo.calls[-1].skip_reason == "not_triggered"


def test_selection_is_immutable_and_budget_gap_blocks_normal(case):
    from pipe_inspector.agent.recall_policy import decide_image_recall

    acts, repo, run, _, _, observation = case
    run["config"]["open_scan"]["max_frames"] = 0
    # Fixed policy settings mismatch must fail before selection/model use.
    with pytest.raises(ApplicationError, match="configuration"):
        ActivityEnvironment().run(acts.select_open_scan, run)
    run["config"]["open_scan"]["max_frames"] = acts.settings.open_scan_max_frames
    run["config"]["plan"] = {"budget": {"max_tool_calls": 1, "per_event_open_scan": 0}}
    plan = ActivityEnvironment().run(acts.select_open_scan, run)
    assert plan["incomplete"] and not plan["selected"]
    repo.steps["detect"] = None
    assert ActivityEnvironment().run(acts.select_open_scan, run) == plan
    base = decide_image_recall(
        repo.task_id,
        repo.task.input_asset,
        observation,
        ImageReview.model_validate(repo.steps["verify"]["review"]),
    )
    result = apply_open_results(repo, run, base)
    assert result.inspection_result == "inconclusive"
    assert not result.coverage.negative_audit_complete


def test_unselected_index_rejected_without_call(case):
    acts, _, run, adapter, _, _ = case
    with pytest.raises(ApplicationError):
        ActivityEnvironment().run(acts.scan_open_candidate, {"run": run, "index": 8})
    adapter.predict_image.assert_not_called()


def test_failure_is_recorded_unloads_and_cannot_be_normal(case):
    from pipe_inspector.agent.recall_policy import decide_image_recall

    acts, repo, run, adapter, _, observation = case
    env = ActivityEnvironment()
    env.run(acts.select_open_scan, run)
    adapter.predict_image.side_effect = RuntimeError("private detail")
    with pytest.raises(ApplicationError) as error:
        env.run(acts.scan_open_candidate, {"run": run, "index": 0})
    assert "private detail" not in str(error.value)
    adapter.unload.assert_called_once()
    assert repo.calls[-1].status == "failed"
    base = decide_image_recall(
        repo.task_id,
        repo.task.input_asset,
        observation,
        ImageReview.model_validate(repo.steps["verify"]["review"]),
    )
    result = apply_open_results(repo, run, base)
    assert result.needs_review and result.inspection_result == "inconclusive"


def test_open_output_must_match_original_dimensions(case):
    acts, repo, run, adapter, _, _ = case
    adapter.predict_image.return_value = adapter.predict_image.return_value.model_copy(
        update={"width": 64}
    )
    env = ActivityEnvironment()
    env.run(acts.select_open_scan, run)
    with pytest.raises(ApplicationError):
        env.run(acts.scan_open_candidate, {"run": run, "index": 0})
    assert "open_scan:0" not in repo.steps


@pytest.mark.parametrize("video", [False, True])
def test_vlm_suspicion_is_not_vetoed_by_basic_quality_score(case, video):
    from pipe_inspector.contracts.video import (
        FrameLocator,
        FrameObservation,
        VideoAggregationState,
        VideoScanResult,
    )

    acts, repo, run, _, _, observation = case
    weak = observation.model_copy(
        update={"quality": observation.quality.model_copy(update={"adequate": False})}
    )
    review = repo.steps["verify"]
    review["review"]["additional_anomalies"] = [
        {"label": "OTHER", "name": "root intrusion", "reason": "visible strands"}
    ]
    if not video:
        repo.steps["detect"] = weak.model_dump(mode="json")
    else:
        run["config"]["operation"] = "video_inspection"
        frame = FrameObservation(**weak.model_dump(), frame_index=0, timestamp_seconds=0)
        repo.get_video_frame = lambda *_: frame
        locator = FrameLocator(
            frame_index=0, timestamp_seconds=0, width=32, height=32, quality=weak.quality
        )
        state = VideoAggregationState(
            source=repo.task.input_asset,
            next_frame=1,
            last_timestamp_seconds=0,
            detector_id="primary",
            model_version="v1",
            label_space_version="five",
            finalized=True,
            audit_frames=(locator,),
        )
        repo.steps["video_scan"] = VideoScanResult(state=state, decode_complete=True).model_dump(
            mode="json"
        )
        repo.steps["video_evidence"] = {
            "frames": [{"frame_index": 0, "asset": repo.task.input_asset.model_dump(mode="json")}]
        }
        repo.steps["video_verify:0"] = review
    selected = ActivityEnvironment().run(acts.select_open_scan, run)
    assert len(selected["selected"]) == 1
    assert selected["selected"][0]["reason"] == "suspected_miss"
