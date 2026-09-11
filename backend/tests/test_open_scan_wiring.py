from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pipe_inspector.agent.intent import compile_inspection_plan
from pipe_inspector.contracts.conversation import ConversationIntent
from pipe_inspector.contracts.planning import ActionKind
from pipe_inspector.vision.detector import UltralyticsDetector
from pipe_inspector.workflow.image import ImageInspectionWorkflow


def test_open_scan_plan_after_review_and_explicit_audit_flag():
    plan = compile_inspection_plan(
        ConversationIntent(kind="inspect", actions=("open_scan",), message="补查"),
        available_actions=tuple(ActionKind),
    )
    assert [s.action.value for s in plan.steps] == [
        "detect",
        "verify",
        "open_scan",
        "summarize",
        "export_report",
    ]
    assert plan.open_anomaly_audit


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


async def test_configured_open_tool_dispatches_but_absent_refuses():
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
        await ConversationDispatcher(repo, starter).handle(
            turn, ConversationIntent(kind="inspect", actions=("open_scan",), message="补查")
        )
        assert starter.start.await_count == int(enabled)
        assert len(repo.created) == int(enabled)


def test_video_reserves_shared_tool_budget_only_when_open_enabled():
    from pipe_inspector.config import Settings
    from pipe_inspector.workflow.start import video_run_config

    assert video_run_config(Settings(_env_file=None))["video_review_budget"] == 32
    settings = Settings(
        _env_file=None, open_detector_path="open.pt", open_text_encoder_path="encoder.ts"
    )
    config = video_run_config(settings)
    assert config["video_review_budget"] == 23  # scan 1 + reviews 23 + open scans 8 = 32


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
