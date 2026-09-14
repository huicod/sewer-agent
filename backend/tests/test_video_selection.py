from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import pytest
from PIL import Image
from pipe_inspector.agent.api_client import APIClientError
from pipe_inspector.agent.video_selection import VideoSceneSelector
from pipe_inspector.contracts.assets import AssetRef
from pipe_inspector.contracts.common import ImageRegion
from pipe_inspector.contracts.detection import Detection, DetectionBatch, DetectionLabel
from pipe_inspector.contracts.inspection import QualitySummary
from pipe_inspector.contracts.video import (
    EventEvidence,
    FrameLocator,
    FrameObservation,
    VideoAggregationState,
    VideoEvent,
    VideoScanResult,
)
from pipe_inspector.contracts.video_selection import (
    VideoReviewSelection,
    VideoSceneAssessment,
    VideoSceneBatch,
)
from pipe_inspector.workflow.video_selection import build_candidate_pool, select_video_review
from pydantic import ValidationError

VIDEO = AssetRef(asset_id=UUID("00000000-0000-4000-8000-000000000201"))


def quality(*, adequate: bool = True) -> QualitySummary:
    return QualitySummary(
        mean_luminance=100,
        contrast=40,
        sharpness=90,
        adequate=adequate,
        reasons=() if adequate else ("too_dark",),
    )


def detection(label: DetectionLabel, *, score: float = 0.5) -> Detection:
    return Detection(
        label=label,
        native_label=label.value,
        score=score,
        bbox_xyxy=(2, 3, 20, 30),
    )


def observation(frame_index: int, item: Detection | None = None, *, adequate=True):
    return FrameObservation(
        frame_index=frame_index,
        timestamp_seconds=float(frame_index),
        detection=DetectionBatch(
            detector_id="fixture",
            model_version="v1",
            label_space_version="five",
            width=64,
            height=48,
            detections=() if item is None else (item,),
        ),
        quality=quality(adequate=adequate),
    )


def locator(frame_index: int) -> FrameLocator:
    return FrameLocator(
        frame_index=frame_index,
        timestamp_seconds=float(frame_index),
        width=64,
        height=48,
        quality=quality(),
    )


def event(event_id: str, frames: tuple[int, ...], *, adequate: bool = True) -> VideoEvent:
    item = detection(DetectionLabel.FS)
    evidence = tuple(
        EventEvidence(
            frame_index=index,
            timestamp_seconds=float(index),
            width=64,
            height=48,
            quality=quality(adequate=adequate),
            region=ImageRegion(width=64, height=48, bbox_xyxy=item.bbox_xyxy),
            score=item.score,
            detection_index=0,
        )
        for index in frames
    )
    return VideoEvent(
        event_id=event_id,
        label=item.label,
        start_frame=min(frames),
        end_frame=max(frames),
        start_seconds=float(min(frames)),
        end_seconds=float(max(frames)),
        observations=max(frames) - min(frames) + 1,
        last_region=evidence[-1].region,
        evidence=evidence,
    )


def scan(events: tuple[VideoEvent, ...], audits: tuple[int, ...]) -> VideoScanResult:
    event_frames = (
        index
        for item in events
        for index in range(item.start_frame, item.end_frame + 1)
    )
    maximum = max((*audits, *event_frames), default=0)
    return VideoScanResult(
        state=VideoAggregationState(
            source=VIDEO,
            detector_id="fixture",
            model_version="v1",
            label_space_version="five",
            next_frame=maximum + 1,
            last_timestamp_seconds=float(maximum),
            adequate_frames=maximum + 1,
            events=events,
            audit_frames=tuple(locator(index) for index in audits),
            finalized=True,
        ),
        decode_complete=True,
    )


def observations(result: VideoScanResult) -> dict[int, FrameObservation]:
    values = {
        item.frame_index: observation(item.frame_index)
        for item in result.state.audit_frames
    }
    for video_event in result.state.events:
        for item in video_event.evidence:
            values[item.frame_index] = observation(
                item.frame_index,
                detection(video_event.label),
                adequate=item.quality.adequate,
            )
    return values


def assessment(frame_index: int, scene: str = "pipe") -> VideoSceneAssessment:
    return VideoSceneAssessment(frame_index=frame_index, scene=scene, reason="fixture reason")


def test_contracts_reject_invalid_duplicate_or_incomplete_indices() -> None:
    with pytest.raises(ValidationError):
        VideoSceneAssessment(frame_index=0, scene="defect", reason="invalid")
    with pytest.raises(ValidationError):
        VideoReviewSelection(
            candidate_count=2,
            pool_frames=(1, 1),
            review_frames=(1,),
            deferred_count=1,
            unreviewed_event_count=0,
            unreviewed_audit_count=0,
        )
    with pytest.raises(ValidationError):
        VideoReviewSelection(
            candidate_count=1,
            pool_frames=(1,),
            review_frames=(2,),
            deferred_count=0,
            unreviewed_event_count=0,
            unreviewed_audit_count=0,
        )


def test_candidate_pool_spreads_events_and_reserves_periodic_audit() -> None:
    result = scan(
        tuple(event(f"event-{index}", (index,)) for index in (1, 2, 3, 97, 98, 99)),
        (10, 30, 50, 70, 90),
    )

    pool = build_candidate_pool(result, observations(result), budget=4)

    assert len(pool) == 8
    assert any(index >= 97 for index in pool)
    assert len(set(pool).intersection({10, 30, 50, 70, 90})) >= 2


def test_audit_only_pool_and_review_use_every_available_slot_from_actual_pool() -> None:
    result = scan((), tuple(range(10)))

    pool = build_candidate_pool(result, observations(result), budget=4)
    selected = select_video_review(result, observations(result), budget=2)

    assert len(pool) == 8
    assert len(selected.review_frames) == 2
    assert set(selected.review_frames).issubset(selected.pool_frames)
    assert selected.unreviewed_audit_count == 8
    assert selected.deferred_count == 8


def test_selection_uses_complete_event_bundles_and_keeps_late_events() -> None:
    result = scan(
        (
            event("early-three", (1, 2, 3)),
            event("middle-one", (50,)),
            event("late-three", (97, 98, 99)),
        ),
        (20, 40, 60, 80),
    )

    selected = select_video_review(result, observations(result), budget=5)

    reviewed = set(selected.review_frames)
    assert reviewed.intersection({1, 2, 3}) in (set(), {1, 2, 3})
    assert reviewed.intersection({97, 98, 99}) in (set(), {97, 98, 99})
    assert reviewed.intersection({20, 40, 60, 80})
    assert 50 in reviewed
    assert selected.unreviewed_event_count == 2


def test_scene_only_defers_fully_invalid_group_and_unknown_never_means_outside() -> None:
    outside = event("outside", (1, 2))
    unknown = event("unknown", (50,))
    pipe = event("pipe", (98,))
    result = scan((outside, unknown, pipe), (20, 80))
    scene = (
        assessment(1, "outside"),
        assessment(2, "unassessable"),
        assessment(50, "unknown"),
        assessment(98, "pipe"),
        assessment(20, "outside"),
        assessment(80, "outside"),
    )

    selected = select_video_review(
        result,
        observations(result),
        scene,
        budget=4,
        scene_calls=1,
        scene_errors=("timeout",),
    )

    assert 50 in selected.review_frames
    assert 98 in selected.review_frames
    assert not set(selected.review_frames).intersection({1, 2})
    assert selected.scene_errors == ("timeout",)


def test_force_review_does_not_scene_defer_and_budget_boundaries_are_exact() -> None:
    result = scan((event("outside", (1,)),), (9,))
    values = observations(result)
    scene = (assessment(1, "outside"), assessment(9, "outside"))

    assert select_video_review(result, values, budget=0).review_frames == ()
    assert len(select_video_review(result, values, budget=1).review_frames) == 1
    forced = select_video_review(
        result, values, scene, force_review=True, budget=32, scene_calls=0
    )
    assert set(forced.review_frames) == {1, 9}


def test_low_quality_single_frame_defect_remains_eligible() -> None:
    video_event = event("dark-defect", (5,), adequate=False)
    result = scan((video_event,), (20, 40, 60))

    selected = select_video_review(result, observations(result), budget=2)

    assert 5 in selected.review_frames


class CapturingClient:
    def __init__(self, response: VideoSceneBatch):
        self.response = response
        self.messages = None
        self.response_model = None

    async def generate(self, messages, response_model):
        self.messages = messages
        self.response_model = response_model
        return self.response


@pytest.mark.asyncio
async def test_scene_selector_sends_bounded_previews_and_schema_without_paths(
    tmp_path: Path,
) -> None:
    source = tmp_path / "untrusted-instruction.png"
    Image.new("RGB", (1024, 256), color=(20, 30, 40)).save(source)
    client = CapturingClient(VideoSceneBatch(assessments=(assessment(7),)))
    pulses = []

    result = await VideoSceneSelector(client).classify(
        ((7, source),), heartbeat=pulses.append
    )

    assert result.assessments[0].frame_index == 7
    assert client.response_model is VideoSceneBatch
    user = client.messages[1]["content"]
    metadata = json.loads(user[0]["text"])
    assert metadata["response_schema"] == VideoSceneBatch.model_json_schema()
    assert metadata["frames"] == [
        {
            "frame_index": 7,
            "original_width": 1024,
            "original_height": 256,
            "preview_width": 512,
            "preview_height": 128,
        }
    ]
    assert str(source) not in json.dumps(client.messages, ensure_ascii=False)
    assert user[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert pulses == [7, 7]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "returned",
    [
        VideoSceneBatch(assessments=(assessment(7), assessment(7))),
        VideoSceneBatch(assessments=(assessment(8),)),
        VideoSceneBatch(assessments=()),
    ],
)
async def test_scene_selector_rejects_duplicate_extra_or_missing_indices(
    tmp_path: Path, returned: VideoSceneBatch
) -> None:
    source = tmp_path / "frame.png"
    Image.new("RGB", (16, 16)).save(source)

    with pytest.raises(APIClientError, match="response_schema_invalid"):
        await VideoSceneSelector(CapturingClient(returned)).classify(((7, source),))
