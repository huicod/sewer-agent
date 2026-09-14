from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pipe_inspector.contracts.assets import AssetRef
from pipe_inspector.contracts.intake import FrameSource, TaskRecord
from pipe_inspector.contracts.tasks import TaskSnapshot
from pipe_inspector.contracts.video import VideoFrameToolPin
from pydantic import ValidationError


def test_frame_source_roundtrips_on_linked_task_record():
    parent_id = uuid4()
    video = AssetRef(asset_id=uuid4())
    frame = AssetRef(asset_id=uuid4())
    source = FrameSource(video_asset=video, frame_index=17, timestamp_seconds=1.7)
    task = TaskRecord(
        request_id=uuid4(),
        session_id=uuid4(),
        input_asset=frame,
        instruction="对选中帧去雾",
        created_at=datetime.now(timezone.utc),
        snapshot=TaskSnapshot(task_id=uuid4(), updated_at=datetime.now(timezone.utc)),
        parent_task_id=parent_id,
        frame_source=source,
    )

    assert TaskRecord.model_validate_json(task.model_dump_json()) == task
    assert task.frame_source == source


def test_frame_source_requires_a_real_parent_task():
    with pytest.raises(ValidationError, match="frame source requires parent task"):
        TaskRecord(
            request_id=uuid4(),
            session_id=uuid4(),
            input_asset=AssetRef(asset_id=uuid4()),
            instruction="选中帧工具",
            created_at=datetime.now(timezone.utc),
            snapshot=TaskSnapshot(task_id=uuid4(), updated_at=datetime.now(timezone.utc)),
            frame_source=FrameSource(
                video_asset=AssetRef(asset_id=uuid4()),
                frame_index=1,
                timestamp_seconds=0.1,
            ),
        )


def test_frame_tool_pin_rejects_non_distinct_assets_and_unknown_version():
    ref = AssetRef(asset_id=uuid4())
    values = {
        "version": "video-frame-tool-v1",
        "parent_task_id": uuid4(),
        "video_asset": ref,
        "frame_asset": ref,
        "frame_index": 3,
        "timestamp_seconds": 0.3,
        "width": 32,
        "height": 24,
        "operation": "enhance",
    }
    with pytest.raises(ValidationError):
        VideoFrameToolPin.model_validate(values)
    with pytest.raises(ValidationError):
        VideoFrameToolPin.model_validate(
            values
            | {
                "version": "changed",
                "frame_asset": AssetRef(asset_id=uuid4()),
            }
        )
