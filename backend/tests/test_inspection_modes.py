from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pipe_inspector.agent.conversation import ConversationDispatcher
from pipe_inspector.api.image_evidence import load_image_evidence
from pipe_inspector.config import Settings
from pipe_inspector.contracts.assets import AssetRecord, AssetRef
from pipe_inspector.contracts.conversation import ConversationIntent
from pipe_inspector.contracts.detection import Detection, DetectionBatch
from pipe_inspector.contracts.image_evidence import ImageEvidence
from pipe_inspector.contracts.inspection import ImageObservation, QualitySummary
from pipe_inspector.contracts.intake import TaskRecord, TaskSubmission
from pipe_inspector.contracts.planning import ActionKind, PlanStep, TaskPlan
from pipe_inspector.contracts.reports import TaskReport
from pipe_inspector.contracts.tasks import TaskSnapshot
from pipe_inspector.contracts.turns import TurnInput, TurnRecord
from pipe_inspector.reports import render_html
from pipe_inspector.storage.repository import PostgresRepository, SubmissionConflict
from pipe_inspector.workflow.activities import ImageActivities
from pipe_inspector.workflow.image import ImageInspectionWorkflow
from pipe_inspector.workflow.start import (
    InspectionStarter,
    InspectionStartError,
    run_config,
    validate_existing_admission,
)
from pydantic import ValidationError


def _detector():
    return SimpleNamespace(
        detector_id="fixture-detector",
        model_version="fixture-v1",
        label_space_version="five-v1",
    )


def _task(*, inspection_mode="yolo_vlm", kind="image"):
    now = datetime(2026, 9, 23, tzinfo=timezone.utc)
    asset = AssetRef(asset_id=uuid4())
    task = TaskRecord(
        request_id=uuid4(),
        session_id=uuid4(),
        input_asset=asset,
        instruction="检查",
        inspection_mode=inspection_mode,
        created_at=now,
        snapshot=TaskSnapshot(task_id=uuid4(), updated_at=now),
    )
    return task, SimpleNamespace(kind=kind, parent=None)


def test_public_mode_contracts_default_old_payloads_and_reject_unknown_values():
    common = {
        "request_id": uuid4(),
        "session_id": uuid4(),
        "input_asset": {"asset_id": uuid4()},
        "instruction": "检查",
    }
    old_task = TaskSubmission.model_validate(common)
    explicit_task = TaskSubmission.model_validate(common | {"inspection_mode": "yolo_only"})
    old_turn = TurnInput(request_id=uuid4(), text="检查")
    pure_turn = TurnInput(request_id=uuid4(), text="检查", inspection_mode="yolo_only")

    assert old_task.inspection_mode == old_turn.inspection_mode == "yolo_vlm"
    assert explicit_task.inspection_mode == pure_turn.inspection_mode == "yolo_only"
    with pytest.raises(ValidationError):
        TaskSubmission.model_validate(common | {"inspection_mode": "detector_only"})
    with pytest.raises(ValidationError):
        TurnInput(request_id=uuid4(), text="检查", inspection_mode="detector_only")


def test_task_retry_identity_includes_inspection_mode():
    task, _ = _task(inspection_mode="yolo_only")
    retry = TaskSubmission(
        request_id=task.request_id,
        session_id=task.session_id,
        input_asset=task.input_asset,
        instruction=task.instruction,
        inspection_mode="yolo_vlm",
    )

    with pytest.raises(SubmissionConflict, match="conflicts"):
        validate_existing_admission(task, retry, parent_task_id=None)


def test_pure_run_config_uses_detector_pins_without_vlm_or_review_pins():
    settings = Settings(
        _env_file=None,
        vlm_base_url="https://user:secret@example.invalid/v1?token=bad",
        vlm_model="must-not-be-read",
    )

    config = run_config(
        settings, _detector(), inspection_mode="yolo_only", force_review=True
    )

    assert config["inspection_mode"] == "yolo_only"
    assert config["detector_id"] == "fixture-detector"
    assert config["model_version"] == "fixture-v1"
    assert config["detector_only_policy_version"] == "detector-only-v1"
    assert "vlm_model" not in config
    assert "vlm_base_url" not in config
    assert "verify_call_id" not in config
    assert "image_review_mode" not in config
    assert config["force_review"] is False


@pytest.mark.asyncio
async def test_pure_workflow_never_schedules_verify_activity(monkeypatch):
    calls = []

    async def execute_activity(name, payload, **options):
        calls.append(name)
        if name == "finish_image":
            return {"runtime_status": "completed", "inspection_result": "inconclusive"}
        return None

    monkeypatch.setattr("pipe_inspector.workflow.image.workflow.execute_activity", execute_activity)
    run = {
        "config": {
            "inspection_mode": "yolo_only",
            "gpu_queue": "fixture-gpu",
            "api_timeout_seconds": 1,
        }
    }

    result = await ImageInspectionWorkflow().run(run)

    assert result["runtime_status"] == "completed"
    assert calls == ["publish_image_stage", "detect_image", "finish_image"]


@pytest.mark.asyncio
async def test_start_rejects_pure_video_before_freezing_or_starting_workflow():
    task, asset = _task(inspection_mode="yolo_only", kind="video")
    repo = SimpleNamespace(
        get_task=lambda _: task,
        get_asset=lambda _: asset,
        get_run=pytest.fail,
        ensure_run=pytest.fail,
    )
    connect = AsyncMock()

    with pytest.raises(InspectionStartError) as error:
        await InspectionStarter(Settings(_env_file=None), repo, connect=connect).start(
            task.snapshot.task_id
        )

    assert error.value.code == "inspection_mode_image_only"
    assert error.value.status_code == 409
    connect.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_rejects_frozen_run_mode_different_from_task_mode():
    task, asset = _task(inspection_mode="yolo_only")
    repo = SimpleNamespace(
        get_task=lambda _: task,
        get_asset=lambda _: asset,
        get_run=lambda _: {
            "task_id": str(task.snapshot.task_id),
            "run_id": str(uuid4()),
            "config": {"inspection_mode": "yolo_vlm"},
        },
    )
    connect = AsyncMock()

    with pytest.raises(InspectionStartError) as error:
        await InspectionStarter(Settings(_env_file=None), repo, connect=connect).start(
            task.snapshot.task_id
        )

    assert error.value.code == "configuration_changed"
    connect.assert_not_awaited()


def test_direct_task_admission_rejects_pure_video_before_insert():
    session_id = uuid4()
    video = AssetRecord(
        ref=AssetRef(asset_id=uuid4()),
        kind="video",
        media_type="video/mp4",
        byte_size=10,
        width=10,
        height=10,
        duration_seconds=1,
    )
    responses = [None, (1,), (video.model_dump(mode="json"),)]

    class Result:
        def fetchone(self):
            return responses.pop(0)

    class Connection:
        calls = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, *_args, **_kwargs):
            self.calls += 1
            return Result()

    connection = Connection()
    repo = PostgresRepository(Settings(_env_file=None).database_dsn)
    repo._connect = lambda: connection
    submission = TaskSubmission(
        request_id=uuid4(),
        session_id=session_id,
        input_asset=video.ref,
        instruction="仅用 YOLO 检查视频",
        inspection_mode="yolo_only",
    )

    with pytest.raises(SubmissionConflict):
        repo.create_task(submission)

    assert connection.calls == 3


@pytest.mark.asyncio
async def test_conversation_propagates_mode_and_rejects_pure_video_before_task_creation():
    now = datetime(2026, 9, 23, tzinfo=timezone.utc)
    asset_ref = AssetRef(asset_id=uuid4())
    turn = TurnRecord(
        request_id=uuid4(),
        session_id=uuid4(),
        text="检查",
        input_asset=asset_ref,
        inspection_mode="yolo_only",
        created_at=now,
        updated_at=now,
    )
    created = []
    store = SimpleNamespace(
        list_turns=lambda *_: (),
        get_asset=lambda _: SimpleNamespace(kind="video", parent=None),
        create_task=lambda submission: created.append(submission),
    )
    starter = SimpleNamespace(start=AsyncMock())

    result = await ConversationDispatcher(store, starter).handle(
        turn, ConversationIntent(kind="inspect", message="检查视频")
    )

    assert "仅支持图片" in result["reply"]
    assert created == []
    starter.start.assert_not_awaited()


@pytest.mark.asyncio
async def test_pure_supplement_without_target_refuses_without_dereferencing_task():
    now = datetime(2026, 9, 23, tzinfo=timezone.utc)
    turn = TurnRecord(
        request_id=uuid4(),
        session_id=uuid4(),
        text="追加复检",
        inspection_mode="yolo_only",
        created_at=now,
        updated_at=now,
    )
    store = SimpleNamespace(list_turns=lambda *_: ())
    starter = SimpleNamespace(admit_supplement=AsyncMock(), start=AsyncMock())

    result = await ConversationDispatcher(store, starter).handle(
        turn,
        ConversationIntent(kind="supplement", actions=("verify",), message="追加复检"),
    )

    assert "不能追加复检" in result["reply"]
    assert result["linked_task_id"] is None
    starter.admit_supplement.assert_not_awaited()
    starter.start.assert_not_awaited()


@pytest.mark.asyncio
async def test_pure_selected_frame_is_rejected_before_asset_or_parent_reads():
    now = datetime(2026, 9, 23, tzinfo=timezone.utc)
    turn = TurnRecord(
        request_id=uuid4(),
        session_id=uuid4(),
        text="增强所选帧",
        input_asset=AssetRef(asset_id=uuid4()),
        task_id=uuid4(),
        inspection_mode="yolo_only",
        created_at=now,
        updated_at=now,
    )
    store = SimpleNamespace(
        get_asset=lambda *_: SimpleNamespace(kind="frame"),
        get_task=lambda *_: pytest.fail("pure frame mode must fail before parent reads"),
    )
    starter = SimpleNamespace(admit_frame_tool=AsyncMock(), start=AsyncMock())

    result = await ConversationDispatcher(store, starter).handle(
        turn,
        ConversationIntent(
            kind="inspect",
            actions=("enhance",),
            enhancement={"kind": "dehaze", "roi_xyxy": None},
            message="增强所选帧",
        ),
    )

    assert "纯 YOLO" in result["reply"] and "不能执行" in result["reply"]
    assert result["linked_task_id"] == str(turn.task_id)
    starter.admit_frame_tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_frame_tool_admission_rejects_pure_submission_before_source_reads():
    submission = TaskSubmission(
        request_id=uuid4(),
        session_id=uuid4(),
        input_asset=AssetRef(asset_id=uuid4()),
        instruction="增强所选帧",
        inspection_mode="yolo_only",
    )
    repo = SimpleNamespace(
        find_task_by_request_id=lambda *_: pytest.fail("pure frame tool must fail first")
    )

    with pytest.raises(InspectionStartError) as error:
        await InspectionStarter(Settings(_env_file=None), repo).admit_frame_tool(
            uuid4(), submission, enhancement=object()
        )

    assert error.value.code == "inspection_mode_tool_unsupported"


@pytest.mark.asyncio
async def test_conversation_propagates_pure_mode_to_created_image_task():
    now = datetime(2026, 9, 23, tzinfo=timezone.utc)
    task, asset = _task(inspection_mode="yolo_only")
    turn = TurnRecord(
        request_id=task.request_id,
        session_id=task.session_id,
        text="仅用 YOLO 检查",
        input_asset=task.input_asset,
        inspection_mode="yolo_only",
        created_at=now,
        updated_at=now,
    )
    created = []

    def create_task(submission):
        created.append(submission)
        return task

    store = SimpleNamespace(
        list_turns=lambda *_: (),
        get_asset=lambda _: asset,
        create_task=create_task,
        save_turn_step=lambda *_: None,
        update_turn=lambda *_args, **_kwargs: turn,
    )
    starter = SimpleNamespace(start=AsyncMock())

    await ConversationDispatcher(store, starter).handle(
        turn, ConversationIntent(kind="inspect", message="检查图片")
    )

    assert created[0].inspection_mode == "yolo_only"
    assert starter.start.call_args.kwargs["force_review"] is False
    assert [
        step.action.value for step in starter.start.call_args.kwargs["plan"].steps
    ] == ["detect", "summarize", "export_report"]


def _enhancement_plan():
    return TaskPlan(
        steps=(
            PlanStep(step_id="detect", action=ActionKind.DETECT),
            PlanStep(step_id="verify", action=ActionKind.VERIFY, depends_on=("detect",)),
            PlanStep(step_id="enhance", action=ActionKind.ENHANCE, depends_on=("verify",)),
        ),
        enhancement={"kind": "dehaze", "roi_xyxy": None},
    )


def test_pure_run_rejects_vlm_dependent_tool_plan_before_detector_initialization():
    with pytest.raises(InspectionStartError) as error:
        run_config(
            Settings(_env_file=None),
            plan=_enhancement_plan(),
            inspection_mode="yolo_only",
        )

    assert error.value.code == "inspection_mode_tool_unsupported"
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_pure_parent_rejects_supplement_before_reading_evidence():
    parent, _ = _task(inspection_mode="yolo_only")
    submission = TaskSubmission(
        request_id=uuid4(),
        session_id=parent.session_id,
        input_asset=parent.input_asset,
        instruction="追加复检",
        inspection_mode="yolo_only",
    )
    repo = SimpleNamespace(
        get_task=lambda _: parent,
        load_step=pytest.fail,
        find_task_by_request_id=pytest.fail,
    )

    with pytest.raises(InspectionStartError) as error:
        await InspectionStarter(Settings(_env_file=None), repo).admit_supplement(
            parent.snapshot.task_id, submission
        )

    assert error.value.code == "inspection_mode_tool_unsupported"


def _observation(*, detections=(), adequate=True):
    return ImageObservation(
        detection=DetectionBatch(
            detector_id="fixture-detector",
            model_version="fixture-v1",
            label_space_version="five-v1",
            width=100,
            height=80,
            detections=detections,
        ),
        quality=QualitySummary(
            mean_luminance=80,
            contrast=20,
            sharpness=100,
            adequate=adequate,
            reasons=() if adequate else ("blurred",),
        ),
    )


def test_detector_only_policy_preserves_detection_and_uses_distinct_confirmation():
    from pipe_inspector.agent.detector_only_policy import decide_detector_only

    task_id = uuid4()
    asset = AssetRef(asset_id=uuid4())
    detection = Detection(
        label="FS", native_label="FS", score=0.8125, bbox_xyxy=(10, 11, 50, 61)
    )

    decision = decide_detector_only(task_id, asset, _observation(detections=(detection,)))

    assert decision.inspection_result == "defects_found"
    assert decision.needs_review is False
    assert decision.coverage.negative_audit_complete is False
    finding = decision.findings[0]
    assert finding.canonical_label == detection.label
    assert finding.score == detection.score
    assert finding.evidence[0].region.bbox_xyxy == detection.bbox_xyxy
    assert finding.confirmed_by == "detector_only_rules"
    assert "vlm_review_skipped_by_mode" in decision.reasons


def test_detector_only_zero_detections_are_terminal_inconclusive_not_normal():
    from pipe_inspector.agent.detector_only_policy import decide_detector_only

    decision = decide_detector_only(uuid4(), AssetRef(asset_id=uuid4()), _observation())

    assert decision.findings == ()
    assert decision.inspection_result == "inconclusive"
    assert decision.needs_review is False
    assert decision.coverage.negative_audit_complete is False
    assert "no_five_class_detections" in decision.reasons


class _FinishStore:
    def __init__(self, task, observation):
        self.task = task
        self.steps = {"detect": observation.model_dump(mode="json")}

    def get_task(self, _task_id):
        return self.task

    def load_step(self, _task_id, step):
        return self.steps.get(step)

    def save_step(self, _task_id, step, payload, call=None):
        self.steps.setdefault(step, payload)
        return self.steps[step]

    def list_calls(self, _task_id):
        return ()

    def update_snapshot(self, snapshot):
        self.task = TaskRecord.model_validate(
            self.task.model_dump() | {"snapshot": snapshot.model_dump()}
        )
        return self.task


def test_pure_zero_detection_finishes_without_human_review_wait():
    task, _ = _task(inspection_mode="yolo_only")
    store = _FinishStore(task, _observation())
    run = {
        "task_id": str(task.snapshot.task_id),
        "run_id": str(uuid4()),
        "config": run_config(
            Settings(_env_file=None), _detector(), inspection_mode="yolo_only"
        ),
    }
    activities = ImageActivities(
        Settings(_env_file=None), store, SimpleNamespace(), reviewer=pytest.fail
    )

    result = activities.finish_image({"run": run})

    assert result["runtime_status"] == "completed"
    assert result["inspection_result"] == "inconclusive"
    assert result["coverage"]["negative_audit_complete"] is False
    assert store.load_step(task.snapshot.task_id, "verify") is None


def test_pure_evidence_and_report_name_skipped_vlm_review_explicitly():
    task, _ = _task(inspection_mode="yolo_only")
    decision_task = TaskRecord.model_validate(
        task.model_dump()
        | {
            "snapshot": task.snapshot.model_dump()
            | {
                "runtime_status": "completed",
                "inspection_result": "inconclusive",
                "stage": "finished",
                "stage_progress": 1,
                "overall_progress": 1,
            }
        }
    )
    observation = _observation()
    repo = SimpleNamespace(
        load_step=lambda _task_id, step: (
            observation.model_dump(mode="json") if step == "detect" else None
        )
    )

    evidence = load_image_evidence(repo, decision_task)
    report = TaskReport(task=decision_task, image_evidence=evidence)
    html = render_html(report)

    assert evidence.inspection_mode == "yolo_only"
    assert evidence.review_status == "skipped_by_mode"
    assert "纯 YOLO" in html
    assert "未执行 VLM 复检" in html


@pytest.mark.parametrize(
    ("inspection_mode", "review_status"),
    [("yolo_only", "unavailable"), ("yolo_vlm", "skipped_by_mode")],
)
def test_evidence_rejects_mode_and_review_status_mismatch(inspection_mode, review_status):
    with pytest.raises(ValidationError):
        ImageEvidence(
            task_id=uuid4(),
            input_asset=AssetRef(asset_id=uuid4()),
            inspection_mode=inspection_mode,
            review_status=review_status,
        )
