from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from PIL import Image
from pipe_inspector.agent.api_client import APIClientError
from pipe_inspector.config import Settings
from pipe_inspector.contracts.assets import AssetRef
from pipe_inspector.contracts.common import ImageRegion
from pipe_inspector.contracts.detection import Detection, DetectionBatch, DetectionLabel
from pipe_inspector.contracts.inspection import QualitySummary
from pipe_inspector.contracts.tasks import TaskSnapshot
from pipe_inspector.contracts.video import (
    EventEvidence,
    FrameLocator,
    FrameObservation,
    VideoAggregationState,
    VideoEvent,
    VideoScanResult,
)
from pipe_inspector.contracts.video_selection import VideoSceneAssessment, VideoSceneBatch
from pipe_inspector.storage.repository import MissingRecord
from pipe_inspector.vision.video_decode import DecodedFrame
from pipe_inspector.workflow.start import video_run_config
from pipe_inspector.workflow.video_activities import VideoActivities
from pipe_inspector.workflow.video_selection import classify_scene_batches
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment


class Store:
    def __init__(self):
        self.steps: dict[str, dict] = {}
        self.calls = []
        self.fail_save = False
        self.saves = []

    def load_step(self, task_id, step):
        return self.steps.get(step)

    def save_step(
        self, task_id, step, payload, call=None, *, allow_error_checkpoint=False
    ):
        if self.fail_save:
            raise RuntimeError("database unavailable")
        self.saves.append((step, call, allow_error_checkpoint))
        if step not in self.steps:
            self.steps[step] = payload
            if call is not None:
                self.record_call(call)
        return self.steps[step]

    def record_call(self, call):
        self.calls = [
            saved
            for saved in self.calls
            if (saved.call_id, saved.attempt) != (call.call_id, call.attempt)
        ]
        self.calls.append(call)


class Selector:
    def __init__(self, *, error: BaseException | None = None):
        self.error = error
        self.calls: list[tuple[tuple[int, Path], ...]] = []

    async def classify(self, frames):
        self.calls.append(tuple(frames))
        if self.error is not None:
            raise self.error
        return VideoSceneBatch(
            assessments=tuple(
                VideoSceneAssessment(frame_index=index, scene="pipe", reason="管内")
                for index, _ in frames
            )
        )


def fixture_run(task_id: UUID) -> dict:
    return {
        "task_id": str(task_id),
        "run_id": str(uuid4()),
        "config": {
            "video_selection_version": "video-selection-v1",
            "vlm_model": "fixture-scene",
            "vlm_base_url": "https://scene.invalid/v1",
            "api_timeout_seconds": 60,
            "api_enable_thinking": False,
            "api_max_output_tokens": 512,
            "max_image_bytes": 1_000_000,
        },
    }


def fixture_frames(tmp_path: Path, count: int = 4):
    frames = []
    for index in range(count):
        path = tmp_path / f"{index}.png"
        path.write_bytes(b"png-fixture")
        frames.append((index, AssetRef(asset_id=uuid4()), path))
    return tuple(frames)


@pytest.mark.asyncio
async def test_scene_batch_checkpoint_replays_without_call_and_binds_source_frames(tmp_path):
    store, selector = Store(), Selector()
    task_id, source = uuid4(), AssetRef(asset_id=uuid4())
    run, frames = fixture_run(task_id), fixture_frames(tmp_path)
    pulses = []

    first = await classify_scene_batches(
        store,
        run,
        source,
        frames,
        selector,
        budget=2,
        heartbeat=lambda index, timestamp: pulses.append((index, timestamp)),
    )
    second = await classify_scene_batches(
        store, run, source, frames, selector, budget=2
    )

    assert first == second
    assert len(selector.calls) == 1
    assert first.scene_calls == 1
    assert [item.frame_index for item in first.assessments] == [0, 1, 2, 3]
    checkpoint = store.steps["video_scene:0"]
    assert checkpoint["context"]["source"] == source.model_dump(mode="json")
    assert checkpoint["context"]["frames"] == [
        {"frame_index": index, "asset": asset.model_dump(mode="json")}
        for index, asset, _ in frames
    ]
    assert store.calls[-1].status == "succeeded"
    assert pulses


@pytest.mark.asyncio
async def test_scene_batch_rejects_cached_context_from_other_source(tmp_path):
    store, selector = Store(), Selector()
    task_id, frames = uuid4(), fixture_frames(tmp_path)
    run = fixture_run(task_id)
    await classify_scene_batches(
        store, run, AssetRef(asset_id=uuid4()), frames, selector, budget=2
    )

    with pytest.raises(ApplicationError) as error:
        await classify_scene_batches(
            store, run, AssetRef(asset_id=uuid4()), frames, selector, budget=2
        )

    assert error.value.type == "configuration_changed"
    assert len(selector.calls) == 1


@pytest.mark.asyncio
async def test_scene_error_is_checkpointed_as_unknown_without_deleting_candidates(tmp_path):
    store = Store()
    selector = Selector(error=APIClientError("timeout", retryable=True))
    task_id, source = uuid4(), AssetRef(asset_id=uuid4())
    frames = fixture_frames(tmp_path)

    result = await classify_scene_batches(
        store, fixture_run(task_id), source, frames, selector, budget=2
    )

    assert result.scene_errors == ("timeout",)
    assert {item.scene for item in result.assessments} == {"unknown"}
    assert [item.frame_index for item in result.assessments] == [0, 1, 2, 3]
    assert store.steps["video_scene:0"]["error"] == "timeout"
    assert store.calls[-1].status == "uncertain"
    assert store.calls[-1].error.code == "timeout"
    assert store.saves[-1][1].status == "uncertain"
    assert store.saves[-1][2] is True


@pytest.mark.asyncio
async def test_scene_cancellation_and_checkpoint_failure_propagate(tmp_path):
    task_id, source = uuid4(), AssetRef(asset_id=uuid4())
    frames, run = fixture_frames(tmp_path), fixture_run(task_id)
    with pytest.raises(asyncio.CancelledError):
        await classify_scene_batches(
            Store(), run, source, frames, Selector(error=asyncio.CancelledError()), budget=2
        )

    store = Store()
    store.fail_save = True
    with pytest.raises(RuntimeError, match="database unavailable"):
        await classify_scene_batches(store, run, source, frames, Selector(), budget=2)


def test_video_config_adds_selection_pin_without_changing_review_verdict_pin() -> None:
    detector = SimpleNamespace(
        detector_id="fixture", model_version="v1", label_space_version="five"
    )
    config = video_run_config(Settings(_env_file=None), detector)

    assert config["video_selection_version"] == "video-selection-v1"
    assert config["video_policy_version"] == "video-review-v1"


def test_video_activities_exposes_independent_scene_selector() -> None:
    detector = SimpleNamespace(
        detector_id="fixture", model_version="v1", label_space_version="five"
    )
    selector = Selector()
    activities = VideoActivities(
        Settings(_env_file=None),
        SimpleNamespace(),
        SimpleNamespace(),
        detector=detector,
        scene_selector=selector,
    )

    assert activities.scene_selector is selector


class PrepareStore(Store):
    def __init__(self, task_id, source, result, frame_observations):
        super().__init__()
        self.task = SimpleNamespace(
            input_asset=source,
            snapshot=TaskSnapshot(task_id=task_id, updated_at=datetime.now(timezone.utc)),
        )
        self.steps["video_scan"] = result.model_dump(mode="json")
        self.frames = frame_observations
        self.assets = {}

    def get_task(self, task_id):
        return self.task

    def get_video_frame(self, task_id, index):
        return self.frames[index]

    def update_snapshot(self, snapshot):
        self.task.snapshot = snapshot
        return self.task

    def get_asset(self, ref):
        try:
            return self.assets[(ref.asset_id, ref.version)]
        except KeyError:
            raise MissingRecord from None

    def save_asset(self, asset):
        self.assets[(asset.ref.asset_id, asset.ref.version)] = asset
        return asset


class PrepareMedia:
    max_pixels = 1_000_000

    def __init__(self, root: Path, source: AssetRef):
        self.root, self.source = root, source

    def path_for(self, ref):
        if ref == self.source:
            return self.root / "source.mp4"
        return self.root / f"{ref.asset_id}.png"


def prepare_scan(source: AssetRef):
    weak = Detection(
        label=DetectionLabel.FS,
        native_label="FS",
        score=0.5,
        bbox_xyxy=(2, 3, 20, 30),
    )
    good = QualitySummary(
        mean_luminance=100, contrast=40, sharpness=90, adequate=True, reasons=()
    )

    def observed(index, detections=()):
        return FrameObservation(
            frame_index=index,
            timestamp_seconds=float(index),
            detection=DetectionBatch(
                detector_id="fixture",
                model_version="v1",
                label_space_version="five",
                width=64,
                height=48,
                detections=detections,
            ),
            quality=good,
        )

    def video_event(name, index):
        evidence = EventEvidence(
            frame_index=index,
            timestamp_seconds=float(index),
            width=64,
            height=48,
            quality=good,
            detection_index=0,
            region=ImageRegion(width=64, height=48, bbox_xyxy=weak.bbox_xyxy),
            score=weak.score,
        )
        return VideoEvent(
            event_id=name,
            label=weak.label,
            start_frame=index,
            end_frame=index,
            start_seconds=float(index),
            end_seconds=float(index),
            observations=1,
            last_region=evidence.region,
            evidence=(evidence,),
        )

    audits = tuple(
        FrameLocator(
            frame_index=index,
            timestamp_seconds=float(index),
            width=64,
            height=48,
            quality=good,
        )
        for index in (3, 7)
    )
    result = VideoScanResult(
        state=VideoAggregationState(
            source=source,
            detector_id="fixture",
            model_version="v1",
            label_space_version="five",
            next_frame=10,
            last_timestamp_seconds=9,
            adequate_frames=10,
            events=(video_event("early", 1), video_event("late", 9)),
            audit_frames=audits,
            finalized=True,
        ),
        decode_complete=True,
    )
    return result, {
        1: observed(1, (weak,)),
        3: observed(3),
        7: observed(7),
        9: observed(9, (weak,)),
    }


class PrioritizingSelector(Selector):
    async def classify(self, frames):
        self.calls.append(tuple(frames))
        return VideoSceneBatch(
            assessments=tuple(
                VideoSceneAssessment(
                    frame_index=index,
                    scene="pipe" if index == 9 else "outside",
                    reason="fixture scene",
                )
                for index, _ in frames
            )
        )


@pytest.mark.asyncio
async def test_prepare_video_persists_selection_and_all_event_pngs(
    tmp_path, monkeypatch
):
    task_id, source = uuid4(), AssetRef(asset_id=uuid4())
    result, frame_observations = prepare_scan(source)
    store = PrepareStore(task_id, source, result, frame_observations)
    media, selector = PrepareMedia(tmp_path, source), PrioritizingSelector()
    detector = SimpleNamespace(
        detector_id="fixture", model_version="v1", label_space_version="five"
    )
    settings = Settings(
        _env_file=None,
        vlm_base_url="https://scene.invalid/v1",
        vlm_model="fixture-scene",
        vlm_api_key="fixture-key",
        api_max_output_tokens=512,
    )
    config = video_run_config(settings, detector)
    # This recovery case deliberately tests legacy environment-only configuration drift.
    # Runtime settings revision behavior is covered by test_runtime_activity_settings.
    config.pop("api_settings_revision")
    config["video_review_budget"] = 3
    run = {"task_id": str(task_id), "run_id": str(uuid4()), "config": config}

    def decoded(*args, **kwargs):
        frames = []
        for index in sorted(kwargs["selected_frames"]):
            path = tmp_path / f"decoded-{index}.png"
            Image.new("RGB", (64, 48), color=(index, 20, 30)).save(path)
            frames.append(DecodedFrame(index, float(index), 64, 48, path))
        yield tuple(frames)

    monkeypatch.setattr(
        "pipe_inspector.workflow.video_activities.decode_video_chunks", decoded
    )
    activities = VideoActivities(
        settings,
        store,
        media,
        detector=detector,
        scene_selector=selector,
    )
    env = ActivityEnvironment()

    returned = await asyncio.to_thread(env.run, activities.prepare_video, run)
    replayed = await asyncio.to_thread(env.run, activities.prepare_video, run)

    assert returned == replayed
    assert set(returned) == {"review_frames"}
    assert 9 in returned["review_frames"]
    assert set(returned["review_frames"]).intersection({3, 7})
    evidence = store.steps["video_evidence"]
    assert {item["frame_index"] for item in evidence["frames"]} == {1, 3, 7, 9}
    assert evidence["selection"]["scene_calls"] == 1
    assert evidence["selection"]["review_frames"] == returned["review_frames"]
    assert evidence["selection"]["deferred_count"] == 2
    assert len(selector.calls) == 1
    assert store.task.snapshot.stage == "verifying"
    assert store.task.snapshot.active_tool == "video_scene"
    assert all(record.kind == "frame" for record in store.assets.values())

    # The scene batch is durable before the final evidence checkpoint. A replacement
    # worker may have mutable VLM settings that differ, but replay must consume the
    # batch bound to the original run rather than issue a new request.
    store.steps.pop("video_evidence")
    changed_settings = Settings(
        _env_file=None,
        vlm_base_url="https://scene.invalid/v1",
        vlm_model="fixture-scene",
        vlm_api_key="fixture-key",
        api_timeout_seconds=settings.api_timeout_seconds + 1,
        api_max_output_tokens=512,
    )
    replacement = VideoActivities(
        changed_settings,
        store,
        media,
        detector=detector,
        scene_selector=selector,
    )

    recovered = await asyncio.to_thread(
        ActivityEnvironment().run, replacement.prepare_video, run
    )

    assert recovered == returned
    assert len(selector.calls) == 1

    store.steps.pop("video_evidence")
    store.steps.pop("video_scene:0")
    with pytest.raises(ApplicationError) as error:
        await asyncio.to_thread(
            ActivityEnvironment().run, replacement.prepare_video, run
        )
    assert error.value.type == "configuration_changed"
    assert len(selector.calls) == 1


@pytest.mark.asyncio
async def test_prepare_video_scene_configuration_failure_is_unknown_fallback(
    tmp_path, monkeypatch
):
    task_id, source = uuid4(), AssetRef(asset_id=uuid4())
    result, frame_observations = prepare_scan(source)
    store = PrepareStore(task_id, source, result, frame_observations)
    media = PrepareMedia(tmp_path, source)
    detector = SimpleNamespace(
        detector_id="fixture", model_version="v1", label_space_version="five"
    )
    settings = Settings(_env_file=None)
    config = video_run_config(settings, detector)
    config["video_review_budget"] = 3
    run = {"task_id": str(task_id), "run_id": str(uuid4()), "config": config}

    def decoded(*args, **kwargs):
        frames = []
        for index in sorted(kwargs["selected_frames"]):
            path = tmp_path / f"fallback-{index}.png"
            Image.new("RGB", (64, 48), color=(index, 20, 30)).save(path)
            frames.append(DecodedFrame(index, float(index), 64, 48, path))
        yield tuple(frames)

    monkeypatch.setattr(
        "pipe_inspector.workflow.video_activities.decode_video_chunks", decoded
    )
    activities = VideoActivities(settings, store, media, detector=detector)

    returned = await asyncio.to_thread(
        ActivityEnvironment().run, activities.prepare_video, run
    )

    selection = store.steps["video_evidence"]["selection"]
    assert returned["review_frames"] == selection["review_frames"]
    assert selection["scene_errors"] == ["vlm_not_configured"]
    assert {item["scene"] for item in selection["assessments"]} == {"unknown"}
