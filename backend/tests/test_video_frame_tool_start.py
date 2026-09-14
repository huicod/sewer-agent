from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pipe_inspector.config import Settings
from pipe_inspector.contracts.assets import AssetKind, AssetRecord, AssetRef
from pipe_inspector.contracts.enhancement import EnhancementOptions
from pipe_inspector.contracts.inspection import ImageObservation
from pipe_inspector.contracts.intake import FrameSource, TaskRecord, TaskSubmission
from pipe_inspector.contracts.tasks import TaskSnapshot
from pipe_inspector.contracts.video import FrameObservation, VideoFrameToolPin
from pipe_inspector.workflow.start import InspectionStarter, InspectionStartError


def observed(index=7, timestamp=0.7):
    return FrameObservation.model_validate(
        {
            "frame_index": index,
            "timestamp_seconds": timestamp,
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
        }
    )


def source_records():
    now = datetime.now(timezone.utc)
    video = AssetRecord(
        ref=AssetRef(asset_id=uuid4()),
        kind=AssetKind.VIDEO,
        media_type="video/mp4",
        byte_size=100,
        width=32,
        height=24,
        duration_seconds=2,
    )
    frame = AssetRecord(
        ref=AssetRef(asset_id=uuid4()),
        kind=AssetKind.FRAME,
        media_type="image/png",
        byte_size=100,
        width=32,
        height=24,
        parent=video.ref,
    )
    parent = TaskRecord(
        request_id=uuid4(),
        session_id=uuid4(),
        input_asset=video.ref,
        instruction="检查视频",
        created_at=now,
        snapshot=TaskSnapshot(
            task_id=uuid4(),
            updated_at=now,
            runtime_status="completed",
            stage="finished",
            inspection_result="normal",
            coverage={
                "sufficient_coverage": True,
                "sufficient_quality": True,
                "negative_audit_complete": True,
            },
        ),
    )
    return parent, video, frame


def submission(parent, frame):
    return TaskSubmission(
        request_id=uuid4(),
        session_id=parent.session_id,
        input_asset=frame.ref,
        instruction="对选中帧去雾",
    )


@pytest.mark.asyncio
async def test_admit_frame_enhancement_freezes_exact_video_evidence(tmp_path):
    from pipe_inspector.vision.enhancement import ENHANCEMENT_MODELS

    parent, video, frame = source_records()
    source = observed()
    _, filename = ENHANCEMENT_MODELS["dehaze"]
    (tmp_path / filename).write_bytes(b"fixture")
    child = object()

    def get_asset(ref):
        return {video.ref: video, frame.ref: frame}[ref]

    repo = SimpleNamespace(
        find_task_by_request_id=Mock(return_value=None),
        get_task=Mock(return_value=parent),
        get_asset=Mock(side_effect=get_asset),
        load_step=Mock(
            side_effect=lambda task_id, step: {
                "video_evidence": {
                    "frames": [
                        {
                            "frame_index": source.frame_index,
                            "timestamp_seconds": source.timestamp_seconds,
                            "asset": frame.ref.model_dump(mode="json"),
                        }
                    ]
                }
            }.get(step)
        ),
        get_video_frame=Mock(return_value=source),
        admit_frame_tool=Mock(return_value=child),
    )
    request = submission(parent, frame)

    result = await InspectionStarter(
        Settings(_env_file=None, enhancement_models_dir=tmp_path), repo
    ).admit_frame_tool(
        parent.snapshot.task_id,
        request,
        enhancement=EnhancementOptions(kind="dehaze"),
    )

    assert result is child
    args = repo.admit_frame_tool.call_args.args
    assert args[:2] == (parent.snapshot.task_id, request)
    config = args[2]
    assert "geometry" not in config and config["enhancement"]["options"]["kind"] == "dehaze"
    pin = VideoFrameToolPin.model_validate(config["frame_source"])
    assert pin.video_asset == video.ref and pin.frame_asset == frame.ref
    assert (pin.frame_index, pin.timestamp_seconds, pin.width, pin.height) == (7, 0.7, 32, 24)
    assert pin.operation == "enhance"


@pytest.mark.asyncio
async def test_frame_tool_recovery_returns_existing_child_before_mutable_reads():
    parent, video, frame = source_records()
    request = submission(parent, frame)
    child = TaskRecord(
        **request.model_dump(),
        created_at=datetime.now(timezone.utc),
        snapshot=TaskSnapshot(task_id=uuid4(), updated_at=datetime.now(timezone.utc)),
        parent_task_id=parent.snapshot.task_id,
        frame_source=FrameSource(video_asset=video.ref, frame_index=7, timestamp_seconds=0.7),
    )
    repo = SimpleNamespace(
        find_task_by_request_id=Mock(return_value=child),
        get_task=Mock(side_effect=AssertionError("must not read mutable parent")),
        get_asset=Mock(side_effect=AssertionError("must not revalidate frame")),
        load_step=Mock(side_effect=AssertionError("must not reload evidence")),
        admit_frame_tool=Mock(side_effect=AssertionError("must not readmit")),
    )

    result = await InspectionStarter(Settings(_env_file=None), repo).admit_frame_tool(
        parent.snapshot.task_id,
        request,
        enhancement=EnhancementOptions(kind="dehaze"),
    )

    assert result == child
    repo.get_task.assert_not_called()
    repo.get_asset.assert_not_called()
    repo.load_step.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["missing", "both"])
async def test_frame_tool_requires_exactly_one_explicit_operation(mode):
    kwargs = (
        {}
        if mode == "missing"
        else {
            "enhancement": EnhancementOptions(kind="dehaze"),
            "geometry": SimpleNamespace(operation="relative_depth"),
        }
    )
    with pytest.raises(InspectionStartError):
        await InspectionStarter(Settings(_env_file=None), object()).admit_frame_tool(
            uuid4(), object(), **kwargs
        )


def test_frame_input_is_allowed_only_through_explicit_frame_tool_validation():
    from pipe_inspector.contracts.geometry import GeometryOptions
    from pipe_inspector.workflow.enhancement_config import validate_enhancement_input
    from pipe_inspector.workflow.geometry_config import validate_geometry_input

    _, _, frame = source_records()
    enhancement = EnhancementOptions(kind="dehaze")
    geometry = GeometryOptions(operation="segment", roi_xyxy=(1, 2, 12, 14))
    with pytest.raises(InspectionStartError):
        validate_enhancement_input(frame, enhancement)
    with pytest.raises(InspectionStartError):
        validate_geometry_input(frame, geometry)

    validate_enhancement_input(frame, enhancement, allow_frame=True)
    assert validate_geometry_input(frame, geometry, allow_frame=True).bbox_xyxy == (
        1,
        2,
        12,
        14,
    )
    with pytest.raises(InspectionStartError, match="ROI"):
        validate_geometry_input(
            frame,
            GeometryOptions(operation="segment", finding_id=uuid4()),
            allow_frame=True,
        )


@pytest.mark.asyncio
async def test_start_accepts_frame_only_with_complete_frozen_context():
    from pipe_inspector.agent.recall_policy import decide_image_recall
    from pipe_inspector.workflow.start import run_config

    parent, video, frame = source_records()
    observation = observed()
    child_id = uuid4()
    source = FrameSource(video_asset=video.ref, frame_index=7, timestamp_seconds=0.7)
    child = TaskRecord(
        request_id=uuid4(),
        session_id=parent.session_id,
        input_asset=frame.ref,
        instruction="选中帧去雾",
        created_at=datetime.now(timezone.utc),
        snapshot=TaskSnapshot(task_id=child_id, updated_at=datetime.now(timezone.utc)),
        parent_task_id=parent.snapshot.task_id,
        frame_source=source,
    )
    pin = VideoFrameToolPin(
        parent_task_id=parent.snapshot.task_id,
        video_asset=video.ref,
        frame_asset=frame.ref,
        frame_index=7,
        timestamp_seconds=0.7,
        width=32,
        height=24,
        operation="enhance",
    )
    image_observation = ImageObservation(
        detection=observation.detection, quality=observation.quality
    )
    decision = decide_image_recall(child_id, frame.ref, image_observation, None)
    config = run_config(Settings(_env_file=None), observation.detection, force_review=True) | {
        "operation": "supplement_review",
        "parent_task_id": str(parent.snapshot.task_id),
        "supplement_policy_version": "supplement-policy-v2",
        "frame_source": pin.model_dump(mode="json"),
        "enhancement": {},
    }
    run = {"task_id": str(child_id), "run_id": str(uuid4()), "config": config}
    steps = {
        "detect": image_observation.model_dump(mode="json"),
        "source": {"task": parent.model_dump(mode="json"), "verify": None},
        "frame_source": {
            "pin": pin.model_dump(mode="json"),
            "observation": observation.model_dump(mode="json"),
            "video_verify": None,
            "decision": decision.model_dump(mode="json"),
        },
    }
    repo = SimpleNamespace(
        get_task=Mock(return_value=child),
        get_asset=Mock(return_value=frame),
        get_run=Mock(return_value=run),
        load_step=Mock(side_effect=lambda task_id, step: steps.get(step)),
    )
    client = SimpleNamespace(start_workflow=AsyncMock())

    await InspectionStarter(
        Settings(_env_file=None), repo, connect=AsyncMock(return_value=client)
    ).start(child_id)

    client.start_workflow.assert_awaited_once()
    assert client.start_workflow.call_args.args[0] == "ImageSupplementWorkflow"
