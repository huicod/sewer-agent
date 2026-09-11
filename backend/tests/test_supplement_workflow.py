from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pipe_inspector.config import Settings
from pipe_inspector.contracts.assets import AssetRef
from pipe_inspector.contracts.intake import TaskSubmission
from pipe_inspector.storage.supplements import SupplementNotAllowed
from pipe_inspector.workflow.start import InspectionStarter, InspectionStartError, run_config
from test_supplement_policy import observation


class LinkedStore:
    def __init__(self):
        self.parent_id = uuid4()
        self.task_id = uuid4()
        self.task = SimpleNamespace(
            parent_task_id=self.parent_id,
            input_asset=object(),
            snapshot=SimpleNamespace(task_id=self.task_id, is_terminal=False),
        )
        self.run = {
            "task_id": str(self.task_id),
            "run_id": str(uuid4()),
            "config": run_config(Settings(_env_file=None))
            | {
                "operation": "supplement_review",
                "parent_task_id": str(self.parent_id),
                "force_review": True,
                "supplement_policy_version": "supplement-policy-v2",
            },
        }

    def get_task(self, task_id):
        return self.task

    def get_asset(self, ref):
        return SimpleNamespace(kind="image")

    def get_run(self, task_id):
        return self.run

    def ensure_run(self, *args):
        raise AssertionError(
            "child runs must be admitted atomically, never recreate a detector run"
        )


async def test_child_manual_start_and_retry_use_review_only_workflow():
    repo = LinkedStore()
    client = SimpleNamespace(start_workflow=AsyncMock(side_effect=[OSError(), object()]))
    starter = InspectionStarter(
        Settings(_env_file=None), repo, connect=AsyncMock(return_value=client)
    )
    with pytest.raises(InspectionStartError):
        await starter.start(repo.task_id)
    await starter.start(repo.task_id)
    first, second = client.start_workflow.call_args_list
    assert first == second
    assert first.args[0] == "ImageSupplementWorkflow"
    assert first.kwargs["id"] == f"inspection:{repo.task_id}"


@pytest.mark.parametrize("broken", ["missing", "parent", "force"])
async def test_child_cannot_fall_back_to_detection_for_missing_or_mismatched_pins(broken):
    repo = LinkedStore()
    if broken == "missing":
        repo.run = None
    elif broken == "parent":
        repo.run["config"]["parent_task_id"] = str(uuid4())
    else:
        repo.run["config"]["force_review"] = False
    connect = AsyncMock()
    with pytest.raises(InspectionStartError) as error:
        await InspectionStarter(Settings(_env_file=None), repo, connect=connect).start(repo.task_id)
    assert error.value.code == "supplement_run_invalid"
    connect.assert_not_awaited()


async def test_admission_pins_original_detector_and_current_vlm_without_starting():
    from unittest.mock import Mock

    observed = observation()
    child = object()
    repo = SimpleNamespace(
        load_step=Mock(return_value=observed.model_dump(mode="json")),
        admit_supplement=Mock(return_value=child),
    )
    settings = Settings(_env_file=None, vlm_model="new-api-model")
    connect = AsyncMock()
    parent_id = uuid4()
    submission = TaskSubmission(
        request_id=uuid4(),
        session_id=uuid4(),
        input_asset=AssetRef(asset_id=uuid4()),
        instruction="追加复检",
    )
    result = await InspectionStarter(settings, repo, connect=connect).admit_supplement(
        parent_id, submission
    )
    assert result is child
    args = repo.admit_supplement.call_args.args
    assert args[:2] == (parent_id, submission)
    assert args[2]["detector_id"] == observed.detection.detector_id
    assert args[2]["model_version"] == observed.detection.model_version
    assert args[2]["vlm_model"] == "new-api-model"
    assert args[2]["force_review"] is True
    assert args[2]["parent_task_id"] == str(parent_id)
    assert args[2]["operation"] == "supplement_review"
    connect.assert_not_awaited()


async def test_admission_cannot_reconstruct_missing_detector_evidence():
    from unittest.mock import Mock

    repo = SimpleNamespace(load_step=Mock(return_value=None), admit_supplement=Mock())
    with pytest.raises(SupplementNotAllowed) as error:
        await InspectionStarter(Settings(_env_file=None), repo).admit_supplement(uuid4(), object())
    assert error.value.code == "source_evidence_missing"
    repo.admit_supplement.assert_not_called()


async def test_supplement_workflow_runs_verify_then_finalize_only(monkeypatch):
    from pipe_inspector.workflow.supplement import ImageSupplementWorkflow

    final = {"stage": "finished", "runtime_status": "completed"}
    execute = AsyncMock(side_effect=[{"review": None}, final])
    monkeypatch.setattr("pipe_inspector.workflow.supplement.workflow.execute_activity", execute)
    wf = ImageSupplementWorkflow()
    result = await wf.run(LinkedStore().run)
    assert result == final
    assert [call.args[0] for call in execute.call_args_list] == [
        "verify_image",
        "finish_image_supplement",
    ]
    assert execute.call_args_list[0].kwargs["retry_policy"].maximum_attempts == 3
    assert execute.call_args_list[1].kwargs["retry_policy"].maximum_attempts == 0
    assert wf.current_stage() == "finished"
