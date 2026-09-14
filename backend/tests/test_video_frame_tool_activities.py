from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from pipe_inspector.contracts.assets import AssetKind, AssetRecord, AssetRef
from pipe_inspector.contracts.common import ImageRegion
from pipe_inspector.contracts.enhancement import EnhancementResult, EnhancementReview
from pipe_inspector.contracts.findings import EvidenceRef, Finding
from pipe_inspector.contracts.inspection import ImageObservation, InspectionDecision, QualitySummary
from pipe_inspector.contracts.intake import FrameSource
from pipe_inspector.contracts.tasks import CoverageSummary, TaskSnapshot
from pipe_inspector.contracts.video import VideoFrameToolPin
from pipe_inspector.workflow.activities import ImageActivities
from pipe_inspector.workflow.enhancement_activities import enhancement_baseline
from pipe_inspector.workflow.geometry_activities import geometry_baseline
from pipe_inspector.workflow.supplement_activities import SupplementActivities


def frozen_case():
    child_id = uuid4()
    parent_id = uuid4()
    video = AssetRef(asset_id=uuid4())
    frame = AssetRef(asset_id=uuid4())
    pin = VideoFrameToolPin(
        parent_task_id=parent_id,
        video_asset=video,
        frame_asset=frame,
        frame_index=7,
        timestamp_seconds=0.7,
        width=32,
        height=24,
        operation="enhance",
    )
    finding = Finding(
        finding_id=uuid4(),
        task_id=child_id,
        canonical_label="PL",
        review_status="confirmed",
        source="detector",
        score=0.9,
        confirmed_by="rules",
        evidence=(
            EvidenceRef(
                asset=frame,
                frame_index=7,
                timestamp_seconds=0.7,
            ),
        ),
    )
    decision = InspectionDecision(
        findings=(finding,),
        inspection_result="defects_found",
        coverage=CoverageSummary(
            sufficient_coverage=True,
            sufficient_quality=True,
            negative_audit_complete=True,
        ),
        needs_review=False,
        reasons=(),
    )
    task = SimpleNamespace(
        input_asset=frame,
        parent_task_id=parent_id,
        frame_source=FrameSource(video_asset=video, frame_index=7, timestamp_seconds=0.7),
        snapshot=TaskSnapshot(task_id=child_id, updated_at=datetime.now(timezone.utc)),
    )
    run = {
        "task_id": str(child_id),
        "config": {
            "operation": "supplement_review",
            "frame_source": pin.model_dump(mode="json"),
            "enhancement": {"options": {"kind": "dehaze"}},
        },
    }
    checkpoint = {
        "pin": pin.model_dump(mode="json"),
        "observation": {
            "frame_index": 7,
            "timestamp_seconds": 0.7,
            "detection": {
                "detector_id": "fixture",
                "model_version": "v1",
                "label_space_version": "five",
                "width": 32,
                "height": 24,
                "detections": [],
            },
            "quality": {
                "mean_luminance": 100,
                "contrast": 20,
                "sharpness": 50,
                "adequate": True,
            },
        },
        "video_verify": None,
        "decision": decision.model_dump(mode="json"),
    }
    steps = {"frame_source": checkpoint}
    repo = SimpleNamespace(load_step=lambda task_id, step: steps.get(step))
    return repo, run, task, decision


def test_enhancement_and_geometry_baselines_use_only_frozen_frame_decision():
    repo, run, task, decision = frozen_case()

    assert enhancement_baseline(repo, run, task, None, None) == decision
    geometry_run = run | {
        "config": {key: value for key, value in run["config"].items() if key != "enhancement"}
        | {
            "frame_source": run["config"]["frame_source"] | {"operation": "segment"},
            "geometry": {"options": {"operation": "segment"}},
        }
    }
    checkpoint = repo.load_step(task.snapshot.task_id, "frame_source")
    geometry_repo = SimpleNamespace(
        load_step=lambda task_id, step: {
            "frame_source": {
                **checkpoint,
                "pin": geometry_run["config"]["frame_source"],
            }
        }.get(step)
    )
    assert geometry_baseline(geometry_repo, geometry_run, task, None, None) == decision


class FrameStore:
    def __init__(self, repo, task):
        self.task = task
        self.steps = {
            "frame_source": repo.load_step(task.snapshot.task_id, "frame_source"),
        }

    def get_task(self, task_id):
        return self.task

    def load_step(self, task_id, step):
        return self.steps.get(step)

    def save_step(self, task_id, step, payload, call=None):
        self.steps.setdefault(step, payload)
        return self.steps[step]

    def update_snapshot(self, snapshot):
        self.task.snapshot = snapshot
        return self.task

    def list_calls(self, task_id):
        return ()


def test_final_enhancement_anomaly_keeps_selected_frame_locator():
    repo, run, task, _ = frozen_case()
    store = FrameStore(repo, task)
    frame_observation = store.steps["frame_source"]["observation"]
    observation = ImageObservation.model_validate(
        {
            "detection": frame_observation["detection"],
            "quality": frame_observation["quality"],
        }
    )
    store.steps["detect"] = observation.model_dump(mode="json")
    store.steps["enhance"] = EnhancementResult(
        input_asset=task.input_asset,
        output_asset=AssetRecord(
            ref=AssetRef(asset_id=uuid4()),
            kind=AssetKind.ENHANCED_IMAGE,
            media_type="image/png",
            byte_size=10,
            width=32,
            height=24,
            parent=task.input_asset,
        ),
        kind="dehaze",
        model_id="fixture",
        model_version="v1",
        source_region=ImageRegion(width=32, height=24, bbox_xyxy=(0, 0, 32, 24)),
        quality_before=QualitySummary(mean_luminance=100, contrast=20, sharpness=50, adequate=True),
        quality_after=QualitySummary(mean_luminance=105, contrast=22, sharpness=55, adequate=True),
    ).model_dump(mode="json")
    store.steps["enhancement_review"] = EnhancementReview(
        quality_effect="improved",
        additional_anomalies=(
            {"label": "FS", "reason": "增强后出现疑似腐蚀", "bbox_xyxy": (2, 3, 8, 9)},
        ),
        summary="仅作为待复核线索",
    ).model_dump(mode="json")
    activities = object.__new__(ImageActivities)
    activities.repo = store
    activities.publish_stage = lambda payload: None
    activities._mark_running_calls_uncertain = lambda task_id, code: None
    activities._check_versions = lambda config: None

    final = activities.finish_image({"run": run})

    anomaly = final["findings"][-1]["evidence"][0]
    assert anomaly["asset"] == task.input_asset.model_dump(mode="json")
    assert anomaly["frame_index"] == 7
    assert anomaly["timestamp_seconds"] == 0.7


def test_configuration_change_uses_frozen_frame_findings_only():
    repo, run, task, decision = frozen_case()
    store = FrameStore(repo, task)
    unrelated = Finding.model_validate(
        decision.findings[0].model_dump()
        | {
            "finding_id": uuid4(),
            "task_id": task.parent_task_id,
            "source": "vlm",
            "review_status": "needs_review",
            "confirmed_by": None,
            "evidence": (
                EvidenceRef(
                    asset=task.input_asset,
                    frame_index=99,
                    timestamp_seconds=9.9,
                ),
            ),
        }
    )
    store.steps["source"] = {
        "task": {
            "request_id": str(uuid4()),
            "session_id": str(uuid4()),
            "input_asset": task.frame_source.video_asset.model_dump(mode="json"),
            "instruction": "原视频检查",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "snapshot": TaskSnapshot(
                task_id=task.parent_task_id,
                runtime_status="partial",
                inspection_result="inconclusive",
                updated_at=datetime.now(timezone.utc),
                findings=(unrelated,),
            ).model_dump(mode="json"),
        },
        "verify": None,
    }
    activities = object.__new__(SupplementActivities)
    activities.repo = store
    frame_observation = store.steps["frame_source"]["observation"]
    observation = ImageObservation.model_validate(
        {
            "detection": frame_observation["detection"],
            "quality": frame_observation["quality"],
        }
    )

    findings = activities._configuration_changed_findings(
        task.snapshot.task_id,
        task.input_asset,
        observation,
        run,
    )

    assert findings == decision.findings
