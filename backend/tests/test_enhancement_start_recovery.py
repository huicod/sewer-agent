"""Idempotent enhancement admission recovery at the starter boundary."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from pipe_inspector.config import Settings
from pipe_inspector.contracts.assets import AssetRef
from pipe_inspector.contracts.enhancement import EnhancementOptions
from pipe_inspector.contracts.intake import TaskRecord, TaskSubmission
from pipe_inspector.contracts.tasks import TaskSnapshot
from pipe_inspector.storage.repository import SubmissionConflict
from pipe_inspector.workflow.start import InspectionStarter, InspectionStartError
from test_supplement_policy import observation


def _submission() -> TaskSubmission:
    return TaskSubmission(
        request_id=uuid4(),
        session_id=uuid4(),
        input_asset=AssetRef(asset_id=uuid4()),
        instruction="追加去雾",
    )


def _child(submission: TaskSubmission, parent_id) -> TaskRecord:
    now = datetime.now(timezone.utc)
    return TaskRecord(
        **submission.model_dump(),
        created_at=now,
        snapshot=TaskSnapshot(task_id=uuid4(), updated_at=now),
        parent_task_id=parent_id,
    )


async def test_existing_supplement_returns_before_rebuilding_mutable_config(tmp_path):
    submission = _submission()
    parent_id = uuid4()
    existing = _child(submission, parent_id)
    repo = SimpleNamespace(
        find_task_by_request_id=Mock(return_value=existing),
        load_step=Mock(side_effect=AssertionError("must reuse frozen admission")),
        get_asset=Mock(side_effect=AssertionError("must not revalidate mutable inputs")),
        admit_supplement=Mock(side_effect=AssertionError("must not create another child")),
    )

    result = await InspectionStarter(
        Settings(_env_file=None, enhancement_models_dir=tmp_path), repo
    ).admit_supplement(parent_id, submission, enhancement=EnhancementOptions(kind="dehaze"))

    assert result == existing
    repo.find_task_by_request_id.assert_called_once_with(submission.request_id)
    repo.load_step.assert_not_called()
    repo.get_asset.assert_not_called()
    repo.admit_supplement.assert_not_called()


@pytest.mark.parametrize("changed", ["session", "asset", "instruction", "parent"])
async def test_existing_supplement_still_enforces_immutable_identity(tmp_path, changed):
    submission = _submission()
    parent_id = uuid4()
    values = submission.model_dump()
    existing_parent = parent_id
    if changed == "session":
        values["session_id"] = uuid4()
    elif changed == "asset":
        values["input_asset"] = AssetRef(asset_id=uuid4())
    elif changed == "instruction":
        values["instruction"] = "更改后的指令"
    else:
        existing_parent = uuid4()
    existing = _child(TaskSubmission.model_validate(values), existing_parent)
    repo = SimpleNamespace(
        find_task_by_request_id=Mock(return_value=existing),
        load_step=Mock(side_effect=AssertionError("identity must fail first")),
    )

    with pytest.raises(SubmissionConflict):
        await InspectionStarter(
            Settings(_env_file=None, enhancement_models_dir=tmp_path), repo
        ).admit_supplement(
            parent_id, submission, enhancement=EnhancementOptions(kind="dehaze")
        )

    repo.load_step.assert_not_called()


async def test_new_supplement_still_checks_model_before_storage_admission(tmp_path):
    submission = _submission()
    repo = SimpleNamespace(
        find_task_by_request_id=Mock(return_value=None),
        load_step=Mock(return_value=observation().model_dump(mode="json")),
        get_asset=Mock(
            return_value=SimpleNamespace(kind="image", width=640, height=480, parent=None)
        ),
        admit_supplement=Mock(),
    )

    with pytest.raises(InspectionStartError) as caught:
        await InspectionStarter(
            Settings(_env_file=None, enhancement_models_dir=tmp_path), repo
        ).admit_supplement(
            uuid4(), submission, enhancement=EnhancementOptions(kind="dehaze")
        )

    assert caught.value.code == "enhancement_unavailable"
    repo.find_task_by_request_id.assert_called_once_with(submission.request_id)
    repo.admit_supplement.assert_not_called()
