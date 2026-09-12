import io
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pytest
from PIL import Image
from pipe_inspector.config import Settings
from pipe_inspector.contracts.findings import EvidenceRef, Finding
from pipe_inspector.contracts.geometry import GeometryOptions
from pipe_inspector.contracts.inspection import (
    ImageObservation,
    ImageReview,
    QualitySummary,
)
from pipe_inspector.contracts.intake import TaskRecord
from pipe_inspector.contracts.tasks import TaskSnapshot
from pipe_inspector.contracts.tools import ToolCall
from pipe_inspector.media import MediaStore
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment


class RuntimeStore:
    def __init__(self, asset):
        self.task_id = uuid4()
        self.task = SimpleNamespace(
            input_asset=asset.ref,
            parent_task_id=None,
            snapshot=TaskSnapshot(task_id=self.task_id, updated_at=datetime.now(timezone.utc)),
        )
        self.asset = asset
        self.steps = {}
        self.calls: list[ToolCall] = []

    def get_task(self, task_id):
        assert task_id == self.task_id
        return self.task

    def get_asset(self, ref):
        assert ref == self.asset.ref
        return self.asset

    def load_step(self, task_id, step):
        assert task_id == self.task_id
        return self.steps.get(step)

    def save_step(self, task_id, step, payload, call=None):
        assert task_id == self.task_id
        self.steps.setdefault(step, payload)
        if call is not None:
            self.record_call(call)
        return self.steps[step]

    def save_geometry(self, task_id, step, result, call, measurements=None):
        payload = {
            "result": result.model_dump(mode="json"),
            "measurements": None
            if measurements is None
            else measurements.model_dump(mode="json"),
        }
        if step not in self.steps:
            self.steps[step] = payload
            self.record_call(call)
        return self.steps[step]

    def record_call(self, call):
        self.calls = [
            old
            for old in self.calls
            if (old.call_id, old.attempt) != (call.call_id, call.attempt)
        ]
        self.calls.append(call)

    def list_calls(self, task_id):
        return tuple(self.calls)

    def update_snapshot(self, snapshot):
        self.task.snapshot = snapshot
        return self.task


class Segmenter:
    model_id = "facebook/sam2.1-hiera-large"
    model_version = "665f8e2ad61cf5f53d65644ff27c8ee525124610"

    def __init__(self, trace):
        self.trace = trace

    def segment_image(self, image, prompt):
        from pipe_inspector.vision.geometry import MaskPrediction

        self.trace.append("segment")
        mask = np.zeros((6, 8), dtype=bool)
        mask[2:4, 3:6] = True
        return MaskPrediction(mask=mask, score=0.8)

    def unload(self):
        self.trace.append("unload_segment")


class Depth:
    model_id = "depth-anything/Depth-Anything-V2-Small-hf"
    model_version = "5426e4f0f36572d16453bbda7a8389317b1bef99"
    larger_is = "nearer"

    def __init__(self, trace):
        self.trace = trace

    def predict_depth(self, image):
        self.trace.append("depth")
        return np.tile(np.arange(8, dtype=np.float32), (6, 1))

    def unload(self):
        self.trace.append("unload_depth")


@pytest.fixture
def case(tmp_path):
    media = MediaStore(tmp_path / "media")
    stream = io.BytesIO()
    Image.new("RGB", (8, 6), (40, 90, 130)).save(stream, "PNG")
    stream.seek(0)
    asset = media.ingest(stream)
    store = RuntimeStore(asset)
    store.steps["detect"] = ImageObservation(
        detection={
            "detector_id": "fixture",
            "model_version": "v1",
            "label_space_version": "five",
            "width": 8,
            "height": 6,
            "detections": [],
        },
        quality=QualitySummary(
            mean_luminance=80, contrast=20, sharpness=30, adequate=True
        ),
    ).model_dump(mode="json")
    store.steps["verify"] = {
        "review": ImageReview(
            image_assessable=True,
            full_image_audited=True,
            candidates=(),
            additional_anomalies=(),
            summary="fixture review",
        ).model_dump(mode="json"),
        "skipped": False,
    }
    settings = Settings(_env_file=None, geometry_models_dir=tmp_path / "models")
    return settings, store, media


def geometry_run(settings, store, operation="relative_measure"):
    from pipe_inspector.workflow.geometry_config import build_geometry_config
    from pipe_inspector.workflow.start import run_config

    options = GeometryOptions(
        operation=operation,
        roi_xyxy=(2, 1, 7, 5) if operation != "relative_depth" else None,
    )
    detector = SimpleNamespace(
        detector_id="fixture", model_version="v1", label_space_version="five"
    )
    config = run_config(settings, detector)
    config["geometry"] = build_geometry_config(settings, options, check_files=False)
    return {
        "task_id": str(store.task_id),
        "run_id": str(uuid4()),
        "config": config,
    }


def test_relative_measure_persists_mask_then_same_source_depth_and_measurements(case):
    from pipe_inspector.workflow.geometry_activities import GeometryActivities

    settings, store, media = case
    trace = []
    def segment_factory(checkpoint, device):
        return Segmenter(trace)

    def depth_factory(checkpoint, device):
        return Depth(trace)
    activities = GeometryActivities(
        settings,
        store,
        media,
        segmenter_factory=segment_factory,
        depth_factory=depth_factory,
    )
    run = geometry_run(settings, store)
    env = ActivityEnvironment()

    source = env.run(activities.prepare_image_geometry, run)
    segment = env.run(activities.segment_image, run)
    depth = env.run(activities.estimate_relative_depth, run)

    assert source["input_asset"] == store.asset.ref.model_dump(mode="json")
    assert source["prompt"]["bbox_xyxy"] == [2.0, 1.0, 7.0, 5.0]
    assert segment["measurements"]["area_pixels"] == 6
    assert segment["measurements"]["image_plane_fraction"] == 0.125
    assert depth["measurements"]["relative_depth"] == {
        "q10": 3.0,
        "median": 4.0,
        "q90": 5.0,
        "larger_is": "nearer",
        "units": "relative_model_units",
    }
    assert trace == ["segment", "unload_segment", "depth", "unload_depth"]
    assert [call.step_id for call in store.calls] == ["geometry_segment", "geometry_depth"]
    assert store.calls[0].tool_name == "segment"
    assert store.calls[1].tool_name == "relative_measure"


@pytest.mark.parametrize(
    ("operation", "step"),
    [("segment", "geometry_segment"), ("relative_depth", "geometry_depth")],
)
def test_cached_geometry_checkpoint_precedes_model_or_configuration_access(
    case, operation, step
):
    from pipe_inspector.workflow.geometry_activities import GeometryActivities

    settings, store, media = case
    winner = {"result": {"winner": True}, "measurements": None}
    store.steps[step] = winner
    run = geometry_run(settings, store, operation)
    run["config"]["geometry"]["version"] = "stale"

    def must_not_load(*args, **kwargs):
        pytest.fail("a committed geometry checkpoint must win before model validation")

    activities = GeometryActivities(
        settings,
        store,
        media,
        segmenter_factory=must_not_load,
        depth_factory=must_not_load,
    )
    method = (
        activities.segment_image
        if operation == "segment"
        else activities.estimate_relative_depth
    )
    assert ActivityEnvironment().run(method, run) == winner


def test_geometry_failure_keeps_original_decision_and_cannot_enter_human_gate(case):
    from pipe_inspector.workflow.activities import ImageActivities

    settings, store, media = case
    run = geometry_run(settings, store, "segment")
    run["config"]["human_review_version"] = "human-review-v1"
    activities = ImageActivities.__new__(ImageActivities)
    activities.settings, activities.repo, activities.media = settings, store, media
    activities.detector = SimpleNamespace()
    activities.reviewer = None

    result = ActivityEnvironment().run(
        activities.finish_image,
        {"run": run, "error": None, "geometry_error": "segmentation_failed"},
    )

    assert result["runtime_status"] == "partial"
    assert result["inspection_result"] == "normal"
    assert result["stage"] == "finished"
    assert "tool_error:segmentation_failed" in result["waiting_reason"]
    assert "review_error" not in result["waiting_reason"]


def test_required_geometry_missing_after_original_review_failure_stays_partial(case):
    from pipe_inspector.workflow.activities import ImageActivities

    settings, store, media = case
    store.steps.pop("verify")
    run = geometry_run(settings, store, "segment")
    run["config"]["human_review_version"] = "human-review-v1"
    activities = ImageActivities.__new__(ImageActivities)
    activities.settings, activities.repo, activities.media = settings, store, media
    activities.detector = SimpleNamespace()
    activities.reviewer = None

    result = ActivityEnvironment().run(
        activities.finish_image,
        {"run": run, "error": "vlm_not_configured", "geometry_error": None},
    )

    assert result["runtime_status"] == "partial"
    assert result["stage"] == "finished"
    assert "review_error:vlm_not_configured" in result["waiting_reason"]
    assert "tool_error:geometry_not_run" in result["waiting_reason"]


def test_geometry_model_exception_records_only_a_safe_failure_code(case):
    from pipe_inspector.workflow.geometry_activities import GeometryActivities

    settings, store, media = case
    run = geometry_run(settings, store, "segment")

    class Broken(Segmenter):
        def segment_image(self, image, prompt):
            raise OSError("private path /srv/models/secret")

    activities = GeometryActivities(
        settings,
        store,
        media,
        segmenter_factory=lambda checkpoint, device: Broken([]),
    )
    env = ActivityEnvironment()
    env.run(activities.prepare_image_geometry, run)

    with pytest.raises(ApplicationError) as caught:
        env.run(activities.segment_image, run)

    assert caught.value.type == "segmentation_failed"
    failed = store.calls[-1]
    assert failed.error.code == "segmentation_failed"
    assert "/srv/" not in failed.error.message


def test_linked_finding_geometry_freezes_parent_baseline_without_new_review(case):
    from pipe_inspector.agent.supplement_policy import SUPPLEMENT_POLICY_VERSION
    from pipe_inspector.workflow.geometry_activities import GeometryActivities
    from pipe_inspector.workflow.geometry_config import build_geometry_config
    from pipe_inspector.workflow.start import run_config

    settings, store, media = case
    parent_id = uuid4()
    finding_id = uuid4()
    region = {
        "width": 8,
        "height": 6,
        "bbox_xyxy": [2.0, 1.0, 7.0, 5.0],
    }
    finding = Finding(
        finding_id=finding_id,
        task_id=parent_id,
        canonical_label="AJ",
        review_status="confirmed",
        source="detector",
        score=0.9,
        evidence=(EvidenceRef(asset=store.asset.ref, region=region),),
        confirmed_by="rules",
    )
    parent = TaskRecord(
        request_id=uuid4(),
        session_id=uuid4(),
        input_asset=store.asset.ref,
        instruction="frozen parent",
        created_at=datetime.now(timezone.utc),
        snapshot=TaskSnapshot(
            task_id=parent_id,
            runtime_status="completed",
            inspection_result="defects_found",
            stage="finished",
            stage_progress=1,
            overall_progress=1,
            processed_frames=1,
            updated_at=datetime.now(timezone.utc),
            findings=(finding,),
            coverage={
                "sufficient_coverage": True,
                "sufficient_quality": True,
                "negative_audit_complete": True,
            },
        ),
    )
    store.task.parent_task_id = parent_id
    store.steps = {"source": {"task": parent.model_dump(mode="json"), "verify": None}}
    detector = SimpleNamespace(
        detector_id="fixture", model_version="v1", label_space_version="five"
    )
    config = run_config(settings, detector, force_review=True)
    config.update(
        operation="supplement_review",
        parent_task_id=str(parent_id),
        supplement_policy_version=SUPPLEMENT_POLICY_VERSION,
    )
    config["geometry"] = build_geometry_config(
        settings,
        GeometryOptions(operation="segment", finding_id=finding_id),
        check_files=False,
    )
    run = {
        "task_id": str(store.task_id),
        "run_id": str(uuid4()),
        "config": config,
    }

    saved = ActivityEnvironment().run(
        GeometryActivities(settings, store, media).prepare_image_geometry,
        run,
    )

    assert saved["prompt"] == region
    decision = saved["decision"]
    assert decision["inspection_result"] == "defects_found"
    assert decision["needs_review"] is False
    assert len(decision["findings"]) == 1
    assert decision["findings"][0]["finding_id"] != str(finding_id)
    assert decision["findings"][0]["task_id"] == str(store.task_id)
    assert decision["findings"][0]["evidence"][0]["region"] == region
