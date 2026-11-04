"""Every cloud activity uses the admitted revision after the browser saves new settings."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from PIL import Image
from pipe_inspector.agent.api_client import APIClientError
from pipe_inspector.contracts.assets import AssetRef
from pipe_inspector.runtime_settings import RuntimeSettingsStore
from pipe_inspector.vision.video_decode import DecodedFrame
from pipe_inspector.workflow.conversation_activities import TurnActivities
from pipe_inspector.workflow.conversation_start import turn_config
from pipe_inspector.workflow.start import run_config, video_run_config
from pipe_inspector.workflow.video_activities import VideoActivities
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment
from test_adaptive_enhancement_workflow import automatic
from test_enhancement_workflow import case as case
from test_image_activities import setup as setup
from test_runtime_settings import settings as settings
from test_runtime_settings import update
from test_turn_workflow import TurnStore
from test_video_selection_activity import PrepareMedia, PrepareStore, prepare_scan


def changed_run(settings, config, mode):
    old = run_config(settings)
    RuntimeSettingsStore(settings).save(update())
    cloud = run_config(settings) if mode == "new" else old
    names = (
        "api_settings_revision",
        "vlm_base_url",
        "vlm_model",
        "api_timeout_seconds",
        "api_enable_thinking",
        "api_max_output_tokens",
        "max_image_bytes",
    )
    result = config | {name: cloud[name] for name in names}
    if mode == "legacy":
        result.pop("api_settings_revision")
    return result


def capture_client(monkeypatch, module):
    captured = []

    def create(endpoint, model, key, **options):
        captured.append((endpoint, model, key.get_secret_value(), options))
        # Stop before any provider request, after the actual activity selected its credentials.
        raise APIClientError("fixture_stop", retryable=False)

    monkeypatch.setattr(f"pipe_inspector.workflow.{module}.OpenAICompatibleClient", create)
    return captured


def assert_selected(captured, mode, role="vision"):
    name = "new" if mode == "new" else "old"
    assert len(captured) == 1
    endpoint, model, key, options = captured[0]
    assert endpoint == f"https://{name}.example/v1"
    assert model == f"{name}-{role}"
    assert key == f"{name}-{role}-secret"
    if mode == "new":
        assert options == {
            "timeout_seconds": 90,
            "enable_thinking": False,
            "max_output_tokens": 2048,
        }


@pytest.mark.parametrize("mode", ["old", "new", "legacy"])
async def test_image_review_loads_its_admitted_credentials(setup, settings, monkeypatch, mode):
    activities, store, run, _ = setup
    activities.settings = settings
    run["config"] = changed_run(settings, run["config"], mode)
    captured = capture_client(monkeypatch, "activities")
    env = ActivityEnvironment()
    env.run(activities.detect_image, run)
    with pytest.raises(ApplicationError) as error:
        await env.run(activities.verify_image, run)
    assert error.value.type == "fixture_stop"
    assert_selected(captured, mode)
    assert "secret" not in str(store.calls)


@pytest.mark.parametrize("mode", ["old", "new", "legacy"])
@pytest.mark.parametrize("select", [False, True])
async def test_enhancement_cloud_paths_keep_revision(case, settings, monkeypatch, mode, select):
    enhanced, _, store, run, _, env = case
    settings.enhancement_models_dir = enhanced.settings.enhancement_models_dir
    enhanced.settings = settings
    run["config"] = changed_run(settings, run["config"], mode)
    if select:
        selector, run, _ = automatic(case)
        selector.selector = None
        captured = capture_client(monkeypatch, "enhancement_selection")
        invoke = selector.select_image_enhancement
    else:
        env.run(enhanced.prepare_image_enhancement, run)
        env.run(enhanced.enhance_image, run)
        enhanced.reviewer = None
        captured = capture_client(monkeypatch, "enhancement_activities")
        invoke = enhanced.review_image_enhancement
    with pytest.raises(ApplicationError) as error:
        await env.run(invoke, run)
    assert error.value.type == "fixture_stop"
    assert_selected(captured, mode)
    assert "secret" not in str(store.calls)


@pytest.mark.parametrize("mode", ["old", "new", "legacy"])
async def test_turn_parse_and_child_dispatch_share_the_admitted_revision(
    settings, monkeypatch, mode
):
    store = TurnStore()
    config = turn_config(settings)
    RuntimeSettingsStore(settings).save(update())
    if mode == "new":
        config = turn_config(settings)
    elif mode == "legacy":
        config.pop("api_settings_revision")
    run = {"request_id": str(store.turn.request_id), "config": config}
    captured = capture_client(monkeypatch, "conversation_activities")
    activities = TurnActivities(settings, store)
    with pytest.raises(ApplicationError) as error:
        await ActivityEnvironment().run(activities.parse_turn, run)
    assert error.value.type == "fixture_stop"
    assert_selected(captured, mode, "text")

    children = []

    def dispatcher(repo, starter):
        children.append(run_config(starter.settings))
        return SimpleNamespace(handle=AsyncMock(return_value={"reply": "fixture"}))

    monkeypatch.setattr(
        "pipe_inspector.workflow.conversation_activities.ConversationDispatcher", dispatcher
    )
    store.steps["intent"] = {"kind": "report", "message": "fixture"}
    await ActivityEnvironment().run(activities.dispatch_turn, run)
    assert children[0].get("api_settings_revision") == config.get("api_settings_revision")
    assert children[0]["vlm_model"] == f"{'new' if mode == 'new' else 'old'}-vision"
    assert "secret" not in str(children)


@pytest.mark.parametrize("mode", ["old", "new", "legacy"])
@pytest.mark.parametrize("scene", [False, True])
async def test_video_scene_and_frame_review_keep_revision(
    settings, monkeypatch, tmp_path, mode, scene
):
    source, task_id = AssetRef(asset_id=uuid4()), uuid4()
    scan, observations = prepare_scan(source)
    store = PrepareStore(task_id, source, scan, observations)
    media = PrepareMedia(tmp_path, source)
    detector = SimpleNamespace(
        detector_id="fixture", model_version="v1", label_space_version="five"
    )
    config = changed_run(settings, video_run_config(settings, detector), mode)
    config["video_review_budget"] = 3
    run = {"task_id": str(task_id), "run_id": str(uuid4()), "config": config}
    activities = VideoActivities(settings, store, media, detector=detector)
    captured = capture_client(monkeypatch, "video_activities")

    def decoded(*args, **kwargs):
        frames = []
        for index in sorted(kwargs["selected_frames"]):
            path = tmp_path / f"decoded-{index}.png"
            Image.new("RGB", (64, 48)).save(path)
            frames.append(DecodedFrame(index, float(index), 64, 48, path))
        yield tuple(frames)

    monkeypatch.setattr("pipe_inspector.workflow.video_activities.decode_video_chunks", decoded)
    if scene:
        with pytest.raises(APIClientError, match="fixture_stop"):
            await asyncio.to_thread(ActivityEnvironment().run, activities.prepare_video, run)
    else:
        store.steps["video_evidence"] = {
            "review_frames": [1],
            "frames": [{"frame_index": 1, "asset": source.model_dump(mode="json")}],
        }
        with pytest.raises(ApplicationError) as error:
            await ActivityEnvironment().run(
                activities.verify_video_frame, {"run": run, "frame_index": 1}
            )
        assert error.value.type == "fixture_stop"
    assert_selected(captured, mode)
    assert "secret" not in str(store.calls)
