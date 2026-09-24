from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pipe_inspector.contracts.assets import AssetRef
from pipe_inspector.contracts.intake import (
    SessionDetail,
    SessionRecord,
    SessionSummary,
    SessionUpdate,
    TaskRecord,
    TaskSubmission,
)
from pipe_inspector.contracts.tasks import TaskSnapshot
from pydantic import ValidationError


def test_session_metadata_contract_defaults_and_patch_validation():
    now = datetime.now(timezone.utc)
    summary = SessionSummary(
        session_id=uuid4(),
        created_at=now,
        title="新对话",
        updated_at=now,
        task_count=0,
        turn_count=0,
    )
    assert summary.custom_title is None
    assert summary.pinned is False
    assert summary.archived is False
    assert SessionUpdate(title="  自定义标题  ").title == "自定义标题"
    assert SessionUpdate(title=None).model_fields_set == {"title"}

    invalid = (
        {},
        {"title": "   "},
        {"title": "x" * 121},
        {"pinned": None},
        {"pinned": "false"},
        {"archived": 1},
        {"unknown": 1},
    )
    for payload in invalid:
        with pytest.raises(ValidationError):
            SessionUpdate.model_validate(payload)


def test_intake_records_roundtrip_without_paths_or_client_runtime_fields():
    session = SessionRecord(
        session_id=uuid4(), created_at=datetime(2026, 9, 9, tzinfo=timezone.utc)
    )
    submission = TaskSubmission(
        request_id=uuid4(),
        session_id=session.session_id,
        input_asset=AssetRef(asset_id=uuid4()),
        instruction="  检查这段管道  ",
    )
    snapshot = TaskSnapshot(task_id=uuid4(), updated_at=datetime(2026, 9, 9, tzinfo=timezone.utc))
    task = TaskRecord(
        **submission.model_dump(),
        created_at=datetime(2026, 9, 9, tzinfo=timezone.utc),
        snapshot=snapshot,
    )
    detail = SessionDetail(session=session, tasks=(task,))

    restored = SessionDetail.model_validate_json(detail.model_dump_json())

    assert restored == detail
    assert restored.tasks[0].instruction == "检查这段管道"
    assert restored.tasks[0].snapshot.task_id == snapshot.task_id
    assert "path" not in restored.model_dump_json()


@pytest.mark.parametrize("instruction", ["", "   ", "x" * 4001])
def test_task_submission_rejects_empty_or_oversized_instruction(instruction):
    with pytest.raises(ValidationError):
        TaskSubmission(
            request_id=uuid4(),
            session_id=uuid4(),
            input_asset=AssetRef(asset_id=uuid4()),
            instruction=instruction,
        )


@pytest.mark.parametrize("extra", [{"path": "/tmp/input"}, {"runtime_status": "running"}])
def test_task_submission_rejects_storage_paths_and_client_runtime_state(extra):
    with pytest.raises(ValidationError):
        TaskSubmission.model_validate(
            {
                "request_id": str(uuid4()),
                "session_id": str(uuid4()),
                "input_asset": {"asset_id": str(uuid4()), "version": 1},
                "instruction": "检查视频",
                **extra,
            }
        )
