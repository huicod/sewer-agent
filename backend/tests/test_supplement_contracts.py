"""Contracts owned by linked image re-review admission."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pipe_inspector.contracts.assets import AssetRef
from pipe_inspector.contracts.intake import TaskRecord
from pipe_inspector.contracts.tasks import TaskSnapshot
from pydantic import ValidationError


def task_record(*, parent_task_id=None) -> TaskRecord:
    task_id = uuid4()
    return TaskRecord(
        request_id=uuid4(),
        session_id=uuid4(),
        input_asset=AssetRef(asset_id=uuid4()),
        instruction="inspect",
        created_at=datetime(2026, 9, 10, tzinfo=timezone.utc),
        snapshot=TaskSnapshot(
            task_id=task_id,
            updated_at=datetime(2026, 9, 10, tzinfo=timezone.utc),
        ),
        parent_task_id=parent_task_id,
    )


def test_parent_task_id_is_optional_and_roundtrips_when_present():
    original = task_record()
    child = task_record(parent_task_id=original.snapshot.task_id)

    assert original.parent_task_id is None
    assert TaskRecord.model_validate_json(child.model_dump_json()) == child


def test_task_record_rejects_self_parent_link():
    record = task_record()

    with pytest.raises(ValidationError, match="task cannot be its own parent"):
        TaskRecord.model_validate(record.model_dump() | {"parent_task_id": record.snapshot.task_id})
