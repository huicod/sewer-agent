from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pipe_inspector.agent.intent import IntentPlanningError, compile_inspection_plan
from pipe_inspector.contracts.conversation import ConversationIntent
from pipe_inspector.contracts.planning import ActionKind
from pipe_inspector.vision.detector import UltralyticsDetector
from pipe_inspector.workflow.image import ImageInspectionWorkflow


def test_retired_open_scan_cannot_be_planned_even_with_stale_capabilities():
    with pytest.raises(IntentPlanningError) as error:
        compile_inspection_plan(
            ConversationIntent(kind="inspect", actions=("open_scan",), message="补查"),
            available_actions=tuple(ActionKind),
        )
    assert error.value.code == "tool_unavailable"


def test_primary_unload_releases_cached_model(monkeypatch):
    detector = UltralyticsDetector("unused.pt")
    model = SimpleNamespace(cpu=Mock(), predictor=object())
    detector._model = model
    detector._class_names = {0: "PL"}
    detector.unload()
    assert detector._model is None and detector._class_names is None
    model.cpu.assert_called_once()
    detector.unload()


@pytest.mark.parametrize("enabled", [False, True])
async def test_workflow_legacy_branch_and_order(monkeypatch, enabled):
    calls = []

    async def execute(name, payload, **options):
        calls.append(name)
        if name == "select_open_scan":
            return {"selected": [{}, {}]}
        if name == "finish_image":
            return {"runtime_status": "completed"}
        return {}

    monkeypatch.setattr("pipe_inspector.workflow.image.workflow.execute_activity", execute)
    config = {"gpu_queue": "gpu", "api_timeout_seconds": 1}
    if enabled:
        config["open_scan"] = {"version": "open-scan-v1"}
    result = await ImageInspectionWorkflow().run({"config": config})
    expected = ["publish_image_stage", "detect_image", "verify_image"]
    if enabled:
        expected += ["select_open_scan", "scan_open_candidate", "scan_open_candidate"]
    assert calls == expected + ["finish_image"]
    assert result["runtime_status"] == "completed"


async def test_open_tool_refuses_before_creation_even_with_legacy_configuration():
    from pipe_inspector.agent.conversation import ConversationDispatcher
    from pipe_inspector.config import Settings
    from test_conversation_dispatch import Store

    for enabled in (False, True):
        repo = Store()
        turn = repo.turn.model_copy(update={"input_asset": repo.asset})
        options = (
            {"open_detector_path": "open.pt", "open_text_encoder_path": "encoder.ts"}
            if enabled
            else {}
        )
        starter = SimpleNamespace(settings=Settings(_env_file=None, **options), start=AsyncMock())
        result = await ConversationDispatcher(repo, starter).handle(
            turn, ConversationIntent(kind="inspect", actions=("open_scan",), message="补查")
        )
        assert "未启动" in result["reply"]
        assert starter.start.await_count == 0
        assert not repo.created and "plan" not in repo.steps


def test_new_image_and_video_ignore_legacy_open_configuration():
    from pipe_inspector.config import Settings
    from pipe_inspector.workflow.start import run_config, video_run_config

    assert video_run_config(Settings(_env_file=None))["video_review_budget"] == 32
    settings = Settings(
        _env_file=None, open_detector_path="open.pt", open_text_encoder_path="encoder.ts"
    )
    config = video_run_config(settings)
    assert config["video_review_budget"] == 32
    assert "open_scan" not in config
    assert "open_scan" not in run_config(settings)


@pytest.mark.parametrize("video", [False, True])
@pytest.mark.parametrize("audit_only", [False, True])
def test_direct_admission_rejects_legacy_open_plan(video, audit_only):
    from pipe_inspector.config import Settings
    from pipe_inspector.contracts.planning import PlanStep, TaskPlan
    from pipe_inspector.workflow.start import InspectionStartError, run_config, video_run_config

    settings = Settings(
        _env_file=None, open_detector_path="open.pt", open_text_encoder_path="encoder.ts"
    )
    plan = TaskPlan(
        open_anomaly_audit=audit_only,
        steps=(PlanStep(step_id="detect", action="detect"),)
        if audit_only
        else (
            PlanStep(step_id="detect", action="detect"),
            PlanStep(step_id="verify", action="verify", depends_on=("detect",)),
            PlanStep(step_id="open", action="open_scan", depends_on=("verify",)),
        ),
    )
    with pytest.raises(InspectionStartError) as error:
        (video_run_config if video else run_config)(settings, plan=plan)
    assert error.value.code == "tool_unavailable"
    assert error.value.status_code == 409


async def test_video_open_scan_honors_pinned_transport_attempts(monkeypatch):
    from pipe_inspector.workflow.video import VideoInspectionWorkflow

    caps = {}

    def start(name, payload, **options):
        caps[name] = options["retry_policy"].maximum_attempts

        async def result():
            if name == "prepare_video":
                return {"review_frames": []}
            if name == "select_open_scan":
                return {"selected": [{}]}
            return {}

        return result()

    monkeypatch.setattr("pipe_inspector.workflow.video.workflow.start_activity", start)
    monkeypatch.setattr(
        "pipe_inspector.workflow.video.workflow.execute_activity",
        AsyncMock(return_value={"runtime_status": "completed"}),
    )
    config = {
        "gpu_queue": "gpu",
        "api_timeout_seconds": 1,
        "open_scan": {"version": "open-scan-v1"},
        "plan": {"budget": {"max_transport_attempts": 1}},
    }
    await VideoInspectionWorkflow().run({"config": config})
    assert caps["scan_open_candidate"] == 1
