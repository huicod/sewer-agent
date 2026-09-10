from __future__ import annotations

from uuid import UUID

import pytest
from pipe_inspector.agent.video_policy import decide_video, select_review_frames
from pipe_inspector.contracts.assets import AssetRef
from pipe_inspector.contracts.common import ImageRegion
from pipe_inspector.contracts.detection import Detection, DetectionBatch, DetectionLabel
from pipe_inspector.contracts.findings import FindingSource, ReviewStatus
from pipe_inspector.contracts.inspection import (
    AdditionalAnomaly,
    CandidateReview,
    ImageReview,
    QualitySummary,
)
from pipe_inspector.contracts.tasks import InspectionResult
from pipe_inspector.contracts.video import (
    EventEvidence,
    FrameLocator,
    FrameObservation,
    VideoAggregationState,
    VideoEvent,
    VideoScanResult,
)

TASK_ID = UUID("00000000-0000-4000-8000-000000000101")
VIDEO = AssetRef(asset_id=UUID("00000000-0000-4000-8000-000000000102"))


def quality(*, adequate: bool = True) -> QualitySummary:
    return QualitySummary(
        mean_luminance=100,
        contrast=30,
        sharpness=80,
        adequate=adequate,
        reasons=() if adequate else ("too_dark",),
    )


def detection(
    label: DetectionLabel,
    *,
    score: float = 0.95,
    box: tuple[float, float, float, float] = (2, 3, 20, 30),
) -> Detection:
    return Detection(label=label, native_label=label.value, score=score, bbox_xyxy=box)


def observation(
    frame_index: int,
    *detections: Detection,
    adequate: bool = True,
) -> FrameObservation:
    return FrameObservation(
        frame_index=frame_index,
        timestamp_seconds=frame_index / 5,
        detection=DetectionBatch(
            detector_id="sewer-yolo26",
            model_version="best-v1",
            label_space_version="sewer-five-v1",
            width=64,
            height=48,
            detections=detections,
        ),
        quality=quality(adequate=adequate),
    )


def evidence(frame_index: int, item: Detection, detection_index: int = 0) -> EventEvidence:
    return EventEvidence(
        frame_index=frame_index,
        timestamp_seconds=frame_index / 5,
        width=64,
        height=48,
        quality=quality(),
        region=ImageRegion(width=64, height=48, bbox_xyxy=item.bbox_xyxy),
        score=item.score,
        detection_index=detection_index,
    )


def event(event_id: str, frame_index: int, item: Detection) -> VideoEvent:
    return VideoEvent(
        event_id=event_id,
        label=item.label,
        start_frame=frame_index,
        end_frame=frame_index,
        start_seconds=frame_index / 5,
        end_seconds=frame_index / 5,
        observations=1,
        last_region=ImageRegion(width=64, height=48, bbox_xyxy=item.bbox_xyxy),
        evidence=(evidence(frame_index, item),),
    )


def multi_frame_event(
    event_id: str,
    label: DetectionLabel,
    items: tuple[tuple[int, Detection, int | None], ...],
) -> VideoEvent:
    first_frame = items[0][0]
    last_frame = items[-1][0]
    event_evidence = tuple(
        EventEvidence(
            frame_index=frame_index,
            timestamp_seconds=frame_index / 5,
            width=64,
            height=48,
            quality=quality(),
            region=ImageRegion(width=64, height=48, bbox_xyxy=item.bbox_xyxy),
            score=item.score,
            detection_index=detection_index,
        )
        for frame_index, item, detection_index in items
    )
    return VideoEvent(
        event_id=event_id,
        label=label,
        start_frame=first_frame,
        end_frame=last_frame,
        start_seconds=first_frame / 5,
        end_seconds=last_frame / 5,
        observations=len(items),
        last_region=event_evidence[-1].region,
        evidence=event_evidence,
    )


def scan(
    *events: VideoEvent,
    audits: tuple[FrameLocator, ...] = (),
    frames: int = 3,
    adequate_frames: int | None = None,
    decode_complete: bool = True,
) -> VideoScanResult:
    return VideoScanResult(
        state=VideoAggregationState(
            source=VIDEO,
            detector_id="sewer-yolo26",
            model_version="best-v1",
            label_space_version="sewer-five-v1",
            next_frame=frames,
            last_timestamp_seconds=(frames - 1) / 5,
            adequate_frames=frames if adequate_frames is None else adequate_frames,
            events=events,
            audit_frames=audits,
            finalized=True,
        ),
        decode_complete=decode_complete,
        error_code=None if decode_complete else "decode_failed",
    )


def locator(frame_index: int, *, adequate: bool = True) -> FrameLocator:
    return FrameLocator(
        frame_index=frame_index,
        timestamp_seconds=frame_index / 5,
        width=64,
        height=48,
        quality=quality(adequate=adequate),
    )


def candidate(index: int, verdict: str, *, proposed_label=None) -> CandidateReview:
    return CandidateReview(
        index=index,
        verdict=verdict,
        proposed_label=proposed_label,
        reason="test review",
    )


def review(
    *candidates: CandidateReview,
    assessable: bool = True,
    audited: bool = True,
    additional: tuple[AdditionalAnomaly, ...] = (),
) -> ImageReview:
    return ImageReview(
        image_assessable=assessable,
        full_image_audited=audited,
        candidates=candidates,
        additional_anomalies=additional,
        summary="review complete",
    )


def frame_asset(frame_index: int) -> AssetRef:
    return AssetRef(asset_id=UUID(f"00000000-0000-4000-8000-{frame_index + 1000:012d}"))


def test_select_review_frames_prioritizes_required_events_then_deduplicated_audits() -> None:
    strong = detection(DetectionLabel.AJ)
    weak_class = detection(DetectionLabel.FS)
    result = scan(
        event("strong", 0, strong),
        event("weak-class", 1, weak_class),
        audits=(locator(1), locator(2)),
    )
    observations = {
        0: observation(0, strong),
        1: observation(1, weak_class),
        2: observation(2),
    }

    assert select_review_frames(result, observations) == (1, 2)


def test_select_review_frames_force_and_budget_are_deterministic() -> None:
    first = detection(DetectionLabel.AJ)
    second = detection(DetectionLabel.PL)
    result = scan(
        event("first", 0, first),
        event("second", 1, second),
        audits=(locator(2),),
    )
    observations = {
        0: observation(0, first),
        1: observation(1, second),
        2: observation(2),
    }

    assert select_review_frames(result, observations, force_review=True, budget=2) == (0, 1)
    assert select_review_frames(result, observations, force_review=True, budget=2) == (0, 1)


def test_single_frame_event_uses_detection_index_and_confirms_original_class() -> None:
    distractor = detection(DetectionLabel.PL, score=0.5, box=(1, 1, 10, 10))
    target = detection(DetectionLabel.FS, box=(20, 10, 40, 30))
    video_event = multi_frame_event("event-fs", DetectionLabel.FS, ((0, target, 1),))

    decision = decide_video(
        TASK_ID,
        scan(video_event, frames=1),
        {0: observation(0, distractor, target)},
        {0: frame_asset(0)},
        {0: review(candidate(0, "reject"), candidate(1, "accept"))},
        {},
    )

    assert decision.inspection_result is InspectionResult.DEFECTS_FOUND
    assert len(decision.findings) == 1
    finding = decision.findings[0]
    assert finding.canonical_label is DetectionLabel.FS
    assert finding.review_status is ReviewStatus.CONFIRMED
    assert finding.confirmed_by == "rules"
    assert finding.evidence[0].asset == frame_asset(0)
    assert finding.evidence[0].frame_index == 0


def test_consistent_multiframe_reviews_confirm_one_event_with_stable_id() -> None:
    first = detection(DetectionLabel.BX, box=(2, 3, 20, 30))
    second = detection(DetectionLabel.BX, box=(3, 3, 21, 30))
    video_event = multi_frame_event(
        "event-bx",
        DetectionLabel.BX,
        ((0, first, 0), (1, second, 0)),
    )
    args = (
        TASK_ID,
        scan(video_event, audits=(locator(0), locator(1)), frames=2),
        {0: observation(0, first), 1: observation(1, second)},
        {},
        {0: review(candidate(0, "accept")), 1: review(candidate(0, "accept"))},
        {},
    )

    left = decide_video(*args)
    right = decide_video(*args)

    assert left.findings[0].review_status is ReviewStatus.CONFIRMED
    assert left.findings[0].finding_id == right.findings[0].finding_id
    assert left.findings[0].finding_id.version == 5
    assert len(left.findings[0].evidence) == 2
    assert left.needs_review is False


def test_mixed_event_reviews_stay_unresolved() -> None:
    first = detection(DetectionLabel.FS, score=0.5)
    second = detection(DetectionLabel.FS, score=0.5, box=(3, 3, 21, 30))
    video_event = multi_frame_event(
        "event-mixed",
        DetectionLabel.FS,
        ((0, first, 0), (1, second, 0)),
    )

    decision = decide_video(
        TASK_ID,
        scan(video_event, audits=(locator(0), locator(1)), frames=2),
        {0: observation(0, first), 1: observation(1, second)},
        {},
        {0: review(candidate(0, "accept")), 1: review(candidate(0, "reject"))},
        {},
    )

    assert decision.findings[0].review_status is ReviewStatus.NEEDS_REVIEW
    assert decision.inspection_result is InspectionResult.INCONCLUSIVE
    assert decision.needs_review is True


def test_all_weak_event_candidates_rejected_rejects_event() -> None:
    first = detection(DetectionLabel.PL, score=0.5)
    second = detection(DetectionLabel.PL, score=0.5, box=(3, 3, 21, 30))
    video_event = multi_frame_event(
        "event-rejected",
        DetectionLabel.PL,
        ((0, first, 0), (1, second, 0)),
    )

    decision = decide_video(
        TASK_ID,
        scan(video_event, audits=(locator(0), locator(1)), frames=2),
        {0: observation(0, first), 1: observation(1, second)},
        {},
        {0: review(candidate(0, "reject")), 1: review(candidate(0, "reject"))},
        {},
    )

    assert decision.findings[0].review_status is ReviewStatus.REJECTED
    assert decision.inspection_result is InspectionResult.NORMAL
    assert decision.needs_review is False


def test_strong_rejection_and_missing_forced_review_never_auto_confirm() -> None:
    strong = detection(DetectionLabel.AJ, score=0.95)
    video_event = event("event-strong", 0, strong)

    rejected = decide_video(
        TASK_ID,
        scan(video_event, frames=1),
        {0: observation(0, strong)},
        {},
        {0: review(candidate(0, "reject"))},
        {},
    )
    missing = decide_video(
        TASK_ID,
        scan(video_event, frames=1),
        {0: observation(0, strong)},
        {},
        {},
        {},
        force_review=True,
    )

    assert rejected.findings[0].review_status is ReviewStatus.NEEDS_REVIEW
    assert missing.findings[0].review_status is ReviewStatus.NEEDS_REVIEW
    assert rejected.needs_review is True
    assert missing.needs_review is True


def test_missing_budgeted_audit_keeps_clean_video_inconclusive() -> None:
    result = scan(audits=(locator(0), locator(1)), frames=2)
    decision = decide_video(
        TASK_ID,
        result,
        {0: observation(0), 1: observation(1)},
        {},
        {0: review()},
        {},
    )

    assert decision.inspection_result is InspectionResult.INCONCLUSIVE
    assert decision.coverage.negative_audit_complete is False
    assert decision.needs_review is True
    assert "audit_review_missing" in decision.reasons


def test_unknown_additional_anomaly_is_pending_stable_and_uses_source_fallback() -> None:
    anomaly = AdditionalAnomaly(
        label="OTHER",
        name="root intrusion",
        bbox_xyxy=(4, 5, 16, 18),
        reason="outside trained labels",
    )
    result = scan(audits=(locator(0),), frames=1)
    args = (TASK_ID, result, {0: observation(0)}, {}, {0: review(additional=(anomaly,))}, {})

    left = decide_video(*args)
    right = decide_video(*args)

    finding = left.findings[0]
    assert finding.finding_id == right.findings[0].finding_id
    assert finding.finding_id.version == 5
    assert finding.canonical_label == "OTHER"
    assert finding.open_label == "root intrusion"
    assert finding.source is FindingSource.VLM
    assert finding.review_status is ReviewStatus.NEEDS_REVIEW
    assert finding.evidence[0].asset == VIDEO
    assert (finding.evidence[0].frame_index, finding.evidence[0].timestamp_seconds) == (0, 0)


def test_complete_clean_audit_is_the_only_normal_path() -> None:
    result = scan(audits=(locator(0),), frames=1)

    decision = decide_video(
        TASK_ID,
        result,
        {0: observation(0)},
        {},
        {0: review()},
        {},
    )

    assert decision.inspection_result is InspectionResult.NORMAL
    assert decision.needs_review is False
    assert decision.coverage.model_dump() == {
        "sufficient_coverage": True,
        "sufficient_quality": True,
        "negative_audit_complete": True,
    }


def test_incomplete_decode_cannot_be_normal_even_with_clean_audit() -> None:
    result = scan(audits=(locator(0),), frames=1, decode_complete=False)

    decision = decide_video(
        TASK_ID,
        result,
        {0: observation(0)},
        {},
        {0: review()},
        {},
    )

    assert decision.inspection_result is InspectionResult.INCONCLUSIVE
    assert decision.coverage.sufficient_coverage is False
    assert decision.needs_review is True
    assert "decode_incomplete" in decision.reasons


def test_legacy_evidence_matches_exact_detection_only_when_unique() -> None:
    item = detection(DetectionLabel.PL, score=0.5)
    video_event = multi_frame_event("legacy", DetectionLabel.PL, ((0, item, None),))
    result = scan(video_event, frames=1)

    unique = decide_video(
        TASK_ID,
        result,
        {0: observation(0, item)},
        {},
        {0: review(candidate(0, "accept"))},
        {},
    )
    ambiguous = decide_video(
        TASK_ID,
        result,
        {0: observation(0, item, item)},
        {},
        {0: review(candidate(0, "accept"), candidate(1, "accept"))},
        {},
    )

    assert unique.findings[0].review_status is ReviewStatus.CONFIRMED
    assert ambiguous.findings[0].review_status is ReviewStatus.NEEDS_REVIEW
    assert "event_detection_association_missing" in ambiguous.reasons


@pytest.mark.parametrize(
    ("reviews", "errors", "quality_ok", "reason"),
    [
        ({0: review(assessable=False)}, {}, False, "audit_not_assessable"),
        ({0: review(audited=False)}, {}, True, "audit_incomplete"),
        ({}, {0: "timeout"}, False, "audit_review_failed"),
    ],
)
def test_unassessable_incomplete_or_failed_audit_is_partial(
    reviews, errors, quality_ok, reason
) -> None:
    result = scan(audits=(locator(0),), frames=1)

    decision = decide_video(
        TASK_ID,
        result,
        {0: observation(0)},
        {},
        reviews,
        errors,
    )

    assert decision.inspection_result is InspectionResult.INCONCLUSIVE
    assert decision.coverage.sufficient_quality is quality_ok
    assert decision.coverage.negative_audit_complete is False
    assert decision.needs_review is True
    assert reason in decision.reasons


def test_confirmed_defect_can_coexist_with_partial_coverage() -> None:
    strong = detection(DetectionLabel.AJ)
    result = scan(event("event-aj", 0, strong), frames=1, decode_complete=False)

    decision = decide_video(TASK_ID, result, {0: observation(0, strong)}, {}, {}, {})

    assert decision.findings[0].review_status is ReviewStatus.CONFIRMED
    assert decision.inspection_result is InspectionResult.DEFECTS_FOUND
    assert decision.coverage.sufficient_coverage is False
    assert decision.needs_review is True


def test_processed_frame_quality_controls_video_quality_coverage() -> None:
    result = scan(
        audits=(locator(0),),
        frames=2,
        adequate_frames=1,
    )

    decision = decide_video(
        TASK_ID,
        result,
        {0: observation(0)},
        {},
        {0: review()},
        {},
    )

    assert decision.coverage.sufficient_quality is False
    assert decision.inspection_result is InspectionResult.INCONCLUSIVE
    assert decision.needs_review is True


def test_incomplete_event_frame_audit_cannot_be_erased_by_clean_periodic_audit() -> None:
    weak = detection(DetectionLabel.PL, score=0.5)
    result = scan(
        event("event-incomplete-audit", 0, weak),
        audits=(locator(1),),
        frames=2,
    )

    decision = decide_video(
        TASK_ID,
        result,
        {0: observation(0, weak), 1: observation(1)},
        {},
        {0: review(candidate(0, "reject"), audited=False), 1: review()},
        {},
    )

    assert decision.findings[0].review_status is ReviewStatus.NEEDS_REVIEW
    assert decision.inspection_result is InspectionResult.INCONCLUSIVE
    assert decision.needs_review is True
    assert "full_image_audit_incomplete" in decision.reasons
