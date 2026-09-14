"""Validation helpers for immutable selected-video-frame tool context."""

from pipe_inspector.contracts.assets import AssetKind
from pipe_inspector.contracts.findings import EvidenceRef, Finding
from pipe_inspector.contracts.inspection import ImageObservation, InspectionDecision
from pipe_inspector.contracts.intake import TaskRecord
from pipe_inspector.contracts.video import FrameObservation, VideoFrameToolPin


def frozen_frame_context(repo, run, task):
    pin = VideoFrameToolPin.model_validate(run["config"]["frame_source"])
    if pin.operation == "enhance":
        valid_operation = "enhancement" in run["config"] and "geometry" not in run["config"]
    else:
        valid_operation = (
            "geometry" in run["config"]
            and "enhancement" not in run["config"]
            and run["config"]["geometry"].get("options", {}).get("operation") == pin.operation
        )
    saved = repo.load_step(task.snapshot.task_id, "frame_source")
    if saved is None or VideoFrameToolPin.model_validate(saved["pin"]) != pin:
        raise ValueError("frame source checkpoint mismatch")
    observation = FrameObservation.model_validate(saved["observation"])
    decision = InspectionDecision.model_validate(saved["decision"])
    source = task.frame_source
    if (
        not valid_operation
        or task.parent_task_id != pin.parent_task_id
        or task.input_asset != pin.frame_asset
        or source is None
        or source.video_asset != pin.video_asset
        or source.frame_index != pin.frame_index
        or source.timestamp_seconds != pin.timestamp_seconds
        or observation.frame_index != pin.frame_index
        or observation.timestamp_seconds != pin.timestamp_seconds
        or (observation.detection.width, observation.detection.height) != (pin.width, pin.height)
    ):
        raise ValueError("frame source identity mismatch")
    if any(
        evidence.asset != pin.frame_asset
        or evidence.frame_index != pin.frame_index
        or evidence.timestamp_seconds != pin.timestamp_seconds
        for finding in decision.findings
        for evidence in finding.evidence
    ):
        raise ValueError("frame decision evidence mismatch")
    return pin, observation, decision


def validate_frame_tool_context(repo, run, task, asset):
    pin, observation, decision = frozen_frame_context(repo, run, task)
    source = TaskRecord.model_validate(repo.load_step(task.snapshot.task_id, "source")["task"])
    detected = ImageObservation.model_validate(repo.load_step(task.snapshot.task_id, "detect"))
    expected = ImageObservation(
        detection=observation.detection,
        quality=observation.quality,
    )
    if (
        asset.kind is not AssetKind.FRAME
        or asset.ref != pin.frame_asset
        or asset.parent != pin.video_asset
        or (asset.width, asset.height) != (pin.width, pin.height)
        or source.snapshot.task_id != pin.parent_task_id
        or source.input_asset != pin.video_asset
        or source.session_id != task.session_id
        or detected != expected
    ):
        raise ValueError("incomplete frame tool context")
    return pin, observation, decision


def localize_frame_decision(repo, run, task, decision: InspectionDecision):
    """Require every final finding to remain evidence from the selected frame."""

    pin, _, _ = frozen_frame_context(repo, run, task)
    findings = []
    for finding in decision.findings:
        evidence = []
        for item in finding.evidence:
            if item.asset != pin.frame_asset or (
                item.frame_index is not None
                and (
                    item.frame_index != pin.frame_index
                    or item.timestamp_seconds != pin.timestamp_seconds
                )
            ):
                raise ValueError("final frame evidence mismatch")
            evidence.append(
                EvidenceRef.model_validate(
                    item.model_dump()
                    | {
                        "frame_index": pin.frame_index,
                        "timestamp_seconds": pin.timestamp_seconds,
                    }
                )
            )
        findings.append(
            Finding.model_validate(finding.model_dump() | {"evidence": tuple(evidence)})
        )
    return InspectionDecision.model_validate(decision.model_dump() | {"findings": tuple(findings)})
