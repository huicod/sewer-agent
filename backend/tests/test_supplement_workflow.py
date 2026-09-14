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


async def test_auto_admission_freezes_available_candidates_without_explicit_choice(tmp_path):
    from unittest.mock import Mock

    from pipe_inspector.vision.enhancement import ENHANCEMENT_MODELS

    observed = observation()
    _, filename = ENHANCEMENT_MODELS["dehaze"]
    (tmp_path / filename).write_bytes(b"fixture")
    child = object()
    repo = SimpleNamespace(
        find_task_by_request_id=Mock(return_value=None),
        load_step=Mock(return_value=observed.model_dump(mode="json")),
        get_asset=Mock(
            return_value=SimpleNamespace(
                kind="image", parent=None, width=32, height=24
            )
        ),
        admit_supplement=Mock(return_value=child),
    )
    settings = Settings(_env_file=None, enhancement_models_dir=tmp_path)
    parent_id = uuid4()
    submission = TaskSubmission(
        request_id=uuid4(),
        session_id=uuid4(),
        input_asset=AssetRef(asset_id=uuid4()),
        instruction="允许按需增强",
    )

    result = await InspectionStarter(settings, repo).admit_supplement(
        parent_id, submission, auto_enhancement=True
    )

    assert result is child
    config = repo.admit_supplement.call_args.args[2]
    assert "enhancement" not in config
    assert set(config["auto_enhancement"]["models"]) == {"dehaze"}
    assert config["auto_enhancement"]["models"]["dehaze"]["options"] is None


@pytest.mark.parametrize("other", ["enhancement", "geometry"])
async def test_auto_admission_is_mutually_exclusive_with_explicit_tools(other):
    kwargs = {"auto_enhancement": True, other: object()}
    with pytest.raises(InspectionStartError):
        await InspectionStarter(Settings(_env_file=None), object()).admit_supplement(
            uuid4(), object(), **kwargs
        )


async def test_auto_admission_retry_returns_frozen_child_before_rebuilding_candidates():
    from datetime import datetime, timezone
    from unittest.mock import Mock

    from pipe_inspector.contracts.intake import TaskRecord
    from pipe_inspector.contracts.tasks import TaskSnapshot

    parent_id = uuid4()
    submission = TaskSubmission(
        request_id=uuid4(),
        session_id=uuid4(),
        input_asset=AssetRef(asset_id=uuid4()),
        instruction="允许按需增强",
    )
    child = TaskRecord(
        **submission.model_dump(),
        created_at=datetime.now(timezone.utc),
        snapshot=TaskSnapshot(task_id=uuid4(), updated_at=datetime.now(timezone.utc)),
        parent_task_id=parent_id,
    )
    repo = SimpleNamespace(
        find_task_by_request_id=Mock(return_value=child),
        load_step=Mock(side_effect=AssertionError("must not reload mutable evidence")),
        get_asset=Mock(side_effect=AssertionError("must not rebuild model candidates")),
        admit_supplement=Mock(side_effect=AssertionError("must not readmit")),
    )

    recovered = await InspectionStarter(Settings(_env_file=None), repo).admit_supplement(
        parent_id, submission, auto_enhancement=True
    )

    assert recovered is child
    repo.load_step.assert_not_called()
    repo.get_asset.assert_not_called()
    repo.admit_supplement.assert_not_called()


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


@pytest.mark.parametrize("selected", [False, True])
async def test_auto_supplement_selects_before_optional_enhancement_without_verify(
    monkeypatch, selected
):
    from pipe_inspector.workflow.supplement import ImageSupplementWorkflow

    calls = []
    pin = {"options": {"kind": "dehaze", "roi_xyxy": None}}

    async def execute(name, payload, **kwargs):
        calls.append((name, payload))
        if name == "select_image_enhancement":
            return {"enhancement": pin if selected else None}
        return {"runtime_status": "completed"} if name == "finish_image_supplement" else {}

    monkeypatch.setattr("pipe_inspector.workflow.supplement.workflow.execute_activity", execute)
    run = {
        "config": {
            "auto_enhancement": {},
            "gpu_queue": "gpu",
            "api_timeout_seconds": 5,
        }
    }

    await ImageSupplementWorkflow().run(run)

    assert [name for name, _ in calls] == [
        "select_image_enhancement",
        *(
            ["prepare_image_enhancement", "enhance_image", "review_image_enhancement"]
            if selected
            else []
        ),
        "finish_image_supplement",
    ]
    assert "enhancement" not in run["config"]
    if selected:
        assert calls[-1][1]["run"]["config"]["enhancement"] == pin


async def test_auto_selection_failure_finalizes_without_child_verify(monkeypatch):
    from pipe_inspector.workflow.supplement import ImageSupplementWorkflow
    from temporalio.exceptions import ActivityError, ApplicationError

    calls = []

    async def execute(name, payload, **kwargs):
        calls.append((name, payload))
        if name == "select_image_enhancement":
            failure = ActivityError(
                "selection failed",
                scheduled_event_id=1,
                started_event_id=2,
                identity="fixture",
                activity_type=name,
                activity_id="fixture",
                retry_state=None,
            )
            failure.__cause__ = ApplicationError(
                "unauthorized", type="authentication_failed", non_retryable=True
            )
            raise failure
        return {"runtime_status": "partial"}

    monkeypatch.setattr("pipe_inspector.workflow.supplement.workflow.execute_activity", execute)
    run = {"config": {"auto_enhancement": {}, "api_timeout_seconds": 5}}

    result = await ImageSupplementWorkflow().run(run)

    assert result["runtime_status"] == "partial"
    assert [name for name, _ in calls] == [
        "select_image_enhancement",
        "finish_image_supplement",
    ]
    assert calls[-1][1]["error"] == "authentication_failed"
