"""Focused safety and model-boundary tests for the browser acceptance server."""

import asyncio
from pathlib import Path

import pytest
from browser_fixtures import (
    BrowserDetector,
    BrowserIntentParser,
    BrowserReviewer,
    create_media_fixtures,
    prepare_output,
)
from PIL import Image
from pipe_inspector.contracts.inspection import ImageObservation, QualitySummary
from pipe_inspector.media import MediaStore
from pipe_inspector.workflow.start import run_config


def test_prepare_output_requires_explicit_gate_and_new_absolute_directory(tmp_path):
    output = tmp_path / "browser-output"
    with pytest.raises(RuntimeError, match="SEWER_RUN_BROWSER_TEST=1"):
        prepare_output({"SEWER_BROWSER_OUTPUT": str(output)})
    with pytest.raises(RuntimeError, match="absolute"):
        prepare_output(
            {"SEWER_RUN_BROWSER_TEST": "1", "SEWER_BROWSER_OUTPUT": "relative"}
        )

    assert prepare_output(
        {"SEWER_RUN_BROWSER_TEST": "1", "SEWER_BROWSER_OUTPUT": str(output)}
    ) == output
    assert output.is_dir()

    with pytest.raises(RuntimeError, match="must not already exist"):
        prepare_output(
            {"SEWER_RUN_BROWSER_TEST": "1", "SEWER_BROWSER_OUTPUT": str(output)}
        )


def test_generated_media_are_fixed_real_ingestible_fixtures(tmp_path):
    output = tmp_path / "new-output"
    output.mkdir()

    image_path, video_path = create_media_fixtures(output)

    assert (output / "assets").is_dir()
    with Image.open(image_path) as image:
        assert image.mode == "RGB"
        assert image.size == (64, 64)
        assert len(set(image.getdata())) > 100
    media = MediaStore(output / "probe-assets")
    with image_path.open("rb") as stream:
        image_asset = media.ingest(stream)
    with video_path.open("rb") as stream:
        video_asset = media.ingest(stream)
    assert (image_asset.kind, image_asset.width, image_asset.height) == (
        "image",
        64,
        64,
    )
    assert (video_asset.kind, video_asset.width, video_asset.height) == (
        "video",
        64,
        64,
    )
    assert video_asset.duration_seconds == pytest.approx(1.2, abs=0.05)


def observation(detector, path: Path) -> ImageObservation:
    return ImageObservation(
        detection=detector.predict_image(path),
        quality=QualitySummary(
            mean_luminance=100,
            contrast=30,
            sharpness=100,
            adequate=True,
        ),
    )


def test_model_substitutes_are_pinned_and_abstain_on_exact_detector_candidate(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    image_path, _ = create_media_fixtures(output)
    detector = BrowserDetector()
    batch = detector.predict_image(image_path)

    assert batch.model_dump(mode="json") == {
        "detector_id": "browser-fixture-detector",
        "model_version": "browser-fixture-detector-v1",
        "label_space_version": "sewer2403-5class-v1",
        "width": 64,
        "height": 64,
        "detections": [
            {
                "label": "AJ",
                "native_label": "AJ",
                "score": 0.65,
                "bbox_xyxy": [10.0, 10.0, 40.0, 40.0],
            }
        ],
    }
    review = asyncio.run(BrowserReviewer().review(image_path, observation(detector, image_path)))
    assert review.image_assessable and review.full_image_audited
    assert [(item.index, item.verdict) for item in review.candidates] == [(0, "abstain")]
    assert run_config(_settings_stub(), detector=detector)["detector_id"] == batch.detector_id


def _settings_stub():
    from pipe_inspector.config import Settings

    return Settings(
        _env_file=None,
        vlm_base_url="https://fixture.invalid/v1",
        vlm_model="browser-fixture-reviewer-v1",
        vlm_api_key="fixture-only",
    )


@pytest.mark.parametrize(
    ("text", "kind", "actions"),
    [
        ("检查上传的 CCTV 图片", "inspect", ("verify",)),
        ("为什么是待人工审核？", "ask", ()),
        ("导出报告", "report", ()),
        ("比较两个已保存任务", "compare", ("compare",)),
    ],
)
def test_intent_substitute_only_selects_bounded_explicit_actions(text, kind, actions):
    intent = asyncio.run(BrowserIntentParser().parse(text, history=[]))

    assert intent.kind == kind
    assert intent.actions == actions
    assert intent.enhancement is None
    assert intent.geometry is None
    assert intent.comparison_reference_task_id is None


def test_detector_rejects_non_64_pixel_fixture(tmp_path):
    path = tmp_path / "small.png"
    Image.new("RGB", (8, 8)).save(path)

    with pytest.raises(ValueError, match="64x64"):
        BrowserDetector().predict_image(path)


@pytest.mark.parametrize("http_failure", [False, True])
def test_shutdown_captures_tasks_dispatched_before_workers_stop(
    tmp_path, monkeypatch, http_failure
):
    from datetime import datetime, timezone
    from types import SimpleNamespace

    import browser_server

    workflow_ids = ["conversation:existing"]
    terminated = []
    snapshots = []
    active_workers = set()

    class Handle:
        def __init__(self, workflow_id):
            self.workflow_id = workflow_id

        async def describe(self):
            return SimpleNamespace(status=SimpleNamespace(name="RUNNING"))

        async def terminate(self, reason):
            terminated.append(self.workflow_id)

    async def connect(*args, **kwargs):
        return SimpleNamespace(get_workflow_handle=Handle)

    class Worker:
        def __init__(self, client, *, task_queue, **kwargs):
            self.queue = task_queue

        async def __aenter__(self):
            active_workers.add(self.queue)
            return self

        async def __aexit__(self, *args):
            if not self.queue.endswith("-gpu"):
                # A dispatch finishes as the CPU worker drains its activities.
                workflow_ids.append("inspection:late-dispatch")
            active_workers.remove(self.queue)

    class Server:
        def __init__(self, config):
            self.started = True

        async def serve(self):
            assert len(active_workers) == 2
            if http_failure:
                raise RuntimeError("test HTTP failure")

    monkeypatch.setattr(browser_server.Client, "connect", connect)
    monkeypatch.setattr(browser_server, "Worker", Worker)
    monkeypatch.setattr(browser_server, "_ManagedServer", Server)
    monkeypatch.setattr(browser_server, "_owned_workflow_ids", lambda repo: tuple(workflow_ids))
    monkeypatch.setattr(browser_server, "create_app", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        browser_server, "_write_snapshot", lambda *args, **kwargs: snapshots.append(kwargs)
    )

    async def run():
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(loop, "add_signal_handler", lambda *args: None)
        monkeypatch.setattr(loop, "remove_signal_handler", lambda *args: None)
        settings = _settings_stub().model_copy(update={"asset_root": tmp_path / "assets"})
        await browser_server._serve(settings, object(), tmp_path, datetime.now(timezone.utc))

    if http_failure:
        with pytest.raises(RuntimeError, match="test HTTP failure"):
            asyncio.run(run())
    else:
        asyncio.run(run())
    assert not active_workers
    assert terminated == workflow_ids
    assert snapshots[0]["terminated_workflows"] == 2
