"""Executable wiring checks for video storage and worker registration."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).parents[2]


def load_script(name):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"test_{name}_module", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_storage_initializer_uses_video_repository(monkeypatch):
    module = load_script("init_storage")
    initialized = []

    class Repository:
        def __init__(self, dsn):
            assert dsn == "fixture-dsn"

        def initialize(self):
            initialized.append(True)

    monkeypatch.setattr(module, "Settings", lambda: SimpleNamespace(database_dsn="fixture-dsn"))
    monkeypatch.setattr(module, "HumanReviewRepository", Repository)

    module.main()

    assert initialized == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["cpu", "gpu"])
async def test_workers_share_one_detector_and_register_video_runtime(monkeypatch, kind):
    # Keep this wiring test independent of the separately implemented video runtime internals.
    video_workflow = ModuleType("pipe_inspector.workflow.video")
    video_workflow.VideoInspectionWorkflow = type("VideoInspectionWorkflow", (), {})
    video_activities = ModuleType("pipe_inspector.workflow.video_activities")
    video_activities.VideoActivities = type("VideoActivities", (), {})
    monkeypatch.setitem(sys.modules, "pipe_inspector.workflow.video", video_workflow)
    monkeypatch.setitem(sys.modules, "pipe_inspector.workflow.video_activities", video_activities)
    module = load_script("inspection_worker")
    settings = SimpleNamespace(
        database_dsn="fixture-dsn",
        asset_root=Path("assets"),
        max_upload_bytes=1,
        max_media_pixels=1,
        media_probe_timeout_seconds=1,
        temporal_address="fixture:7233",
        temporal_namespace="fixture",
        task_queue="fixture-queue",
    )
    repo = object()
    detector = object()
    images = SimpleNamespace(
        detector=detector,
        detect_image=object(),
        publish_stage=object(),
        verify_image=object(),
        finish_image=object(),
    )
    video = SimpleNamespace(
        scan_video=object(),
        prepare_video=object(),
        verify_video_frame=object(),
        finish_video=object(),
    )
    open_scans = SimpleNamespace(scan_open_candidate=object(), select_open_scan=object())
    enhancements = SimpleNamespace(
        enhance_image=object(),
        prepare_image_enhancement=object(),
        review_image_enhancement=object(),
    )
    geometry = SimpleNamespace(
        prepare_image_geometry=object(), segment_image=object(), estimate_relative_depth=object()
    )
    captured = {}

    monkeypatch.setattr(module, "Settings", lambda: settings)
    monkeypatch.setattr(module, "HumanReviewRepository", lambda dsn: repo)
    monkeypatch.setattr(module, "MediaStore", lambda *args, **kwargs: object())
    monkeypatch.setattr(module, "ImageActivities", lambda *args, **kwargs: images)
    monkeypatch.setattr(module, "EnhancementActivities", lambda *args, **kwargs: enhancements)
    monkeypatch.setattr(module, "GeometryActivities", lambda *args, **kwargs: geometry)

    def make_video(*args, **kwargs):
        assert args[1] is repo
        assert kwargs["detector"] is detector
        return video

    monkeypatch.setattr(module, "VideoActivities", make_video)
    monkeypatch.setattr(module, "RetiredOpenScanActivities", lambda *args, **kwargs: open_scans)
    monkeypatch.setattr(
        module,
        "SupplementActivities",
        lambda *args: SimpleNamespace(finish_supplement=object()),
    )
    monkeypatch.setattr(
        module,
        "TurnActivities",
        lambda *args: SimpleNamespace(
            parse_turn=object(), dispatch_turn=object(), finish_turn=object()
        ),
    )

    async def connect(*args, **kwargs):
        return object()

    monkeypatch.setattr(module.Client, "connect", connect)

    class Worker:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)
            self.task_queue = kwargs["task_queue"]

        async def run(self):
            return None

    monkeypatch.setattr(module, "Worker", Worker)

    await module.serve(kind)

    if kind == "gpu":
        assert captured["workflows"] == []
        assert captured["activities"] == [
            images.detect_image,
            video.scan_video,
            open_scans.scan_open_candidate,
            enhancements.enhance_image,
            geometry.segment_image,
            geometry.estimate_relative_depth,
        ]
        assert captured["max_concurrent_activities"] == 1
    else:
        assert module.VideoInspectionWorkflow in captured["workflows"]
        assert open_scans.select_open_scan in captured["activities"]
        assert enhancements.prepare_image_enhancement in captured["activities"]
        assert enhancements.review_image_enhancement in captured["activities"]
        assert geometry.prepare_image_geometry in captured["activities"]
        for activity in (video.prepare_video, video.verify_video_frame, video.finish_video):
            assert activity in captured["activities"]
