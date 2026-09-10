from uuid import UUID

import pytest
from pipe_inspector.agent.recall_policy import (
    RECALL_POLICY_VERSION,
    decide_image_recall,
)
from pipe_inspector.contracts.assets import AssetRef
from pipe_inspector.contracts.detection import Detection, DetectionBatch, DetectionLabel
from pipe_inspector.contracts.findings import FindingSource, ReviewStatus
from pipe_inspector.contracts.inspection import (
    AdditionalAnomaly,
    CandidateReview,
    ImageObservation,
    ImageReview,
    QualitySummary,
)
from pipe_inspector.contracts.tasks import InspectionResult

TASK_ID = UUID("00000000-0000-4000-8000-000000000101")
ASSET = AssetRef(asset_id=UUID("00000000-0000-4000-8000-000000000102"))


def detection(
    label: DetectionLabel = DetectionLabel.AJ,
    score: float = 0.95,
    bbox_xyxy: tuple[float, float, float, float] = (2, 3, 20, 30),
) -> Detection:
    return Detection(
        label=label,
        native_label=label.value,
        score=score,
        bbox_xyxy=bbox_xyxy,
    )


def observation(*detections: Detection, adequate: bool = True) -> ImageObservation:
    return ImageObservation(
        detection=DetectionBatch(
            detector_id="sewer-yolo26",
            model_version="best-v1",
            label_space_version="sewer-five-v1",
            width=64,
            height=48,
            detections=detections,
        ),
        quality=QualitySummary(
            mean_luminance=110,
            contrast=35,
            sharpness=90,
            adequate=adequate,
            reasons=() if adequate else ("too_dark",),
        ),
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
        summary="independent audit complete",
    )


@pytest.mark.parametrize("score", [0.5, 0.95])
def test_vlm_rejection_never_removes_detector_finding(score: float) -> None:
    observed = observation(detection(DetectionLabel.PL, score))

    decision = decide_image_recall(
        TASK_ID,
        ASSET,
        observed,
        review(CandidateReview(index=0, verdict="reject", reason="not visible")),
    )

    finding = decision.findings[0]
    assert RECALL_POLICY_VERSION == "image-recall-v1"
    assert len(decision.findings) == 1
    assert finding.canonical_label is DetectionLabel.PL
    assert finding.score == score
    assert finding.evidence[0].region.bbox_xyxy == (2, 3, 20, 30)
    assert finding.review_status is ReviewStatus.NEEDS_REVIEW
    assert decision.inspection_result is InspectionResult.INCONCLUSIVE
    assert decision.needs_review is True
    assert "candidate_rejection_suggested" in decision.reasons


@pytest.mark.parametrize("verdict", ["revise", "accept"])
def test_vlm_relabel_advice_never_changes_detector_label(verdict: str) -> None:
    observed = observation(detection(DetectionLabel.FS, 0.9))
    raw_review = review(
        CandidateReview(
            index=0,
            verdict=verdict,
            proposed_label=DetectionLabel.BX,
            reason="looks more like deformation",
        )
    )
    original_review = raw_review.model_dump()

    decision = decide_image_recall(TASK_ID, ASSET, observed, raw_review)

    assert decision.findings[0].canonical_label is DetectionLabel.FS
    assert decision.findings[0].review_status is ReviewStatus.NEEDS_REVIEW
    assert decision.inspection_result is InspectionResult.INCONCLUSIVE
    assert "label_revision_suggested" in decision.reasons
    assert raw_review.model_dump() == original_review


@pytest.mark.parametrize(
    ("image_review", "review_error", "reason"),
    [
        (
            review(CandidateReview(index=0, verdict="abstain", reason="uncertain")),
            None,
            "candidate_review_abstained",
        ),
        (review(), None, "candidate_review_missing"),
        (None, None, "required_review_missing"),
        (None, "api_timeout", "review_failed"),
    ],
)
def test_uncertain_missing_or_failed_review_cannot_confirm_detector_finding(
    image_review: ImageReview | None,
    review_error: str | None,
    reason: str,
) -> None:
    decision = decide_image_recall(
        TASK_ID,
        ASSET,
        observation(detection(DetectionLabel.AJ, 0.95)),
        image_review,
        review_error=review_error,
    )

    assert decision.findings[0].review_status is ReviewStatus.NEEDS_REVIEW
    assert decision.findings[0].confirmed_by is None
    assert decision.inspection_result is InspectionResult.INCONCLUSIVE
    assert reason in decision.reasons


def test_assessable_same_class_acceptance_can_confirm_adequate_detection() -> None:
    decision = decide_image_recall(
        TASK_ID,
        ASSET,
        observation(detection(DetectionLabel.BX, 0.9)),
        review(CandidateReview(index=0, verdict="accept", reason="same class visible")),
    )

    assert decision.findings[0].review_status is ReviewStatus.CONFIRMED
    assert decision.findings[0].confirmed_by == "rules"
    assert decision.inspection_result is InspectionResult.DEFECTS_FOUND
    assert decision.needs_review is False


def test_same_class_acceptance_does_not_confirm_inadequate_image() -> None:
    decision = decide_image_recall(
        TASK_ID,
        ASSET,
        observation(detection(DetectionLabel.BX, 0.9), adequate=False),
        review(CandidateReview(index=0, verdict="accept", reason="same class visible")),
    )

    assert decision.findings[0].review_status is ReviewStatus.NEEDS_REVIEW
    assert decision.inspection_result is InspectionResult.INCONCLUSIVE
    assert "quality_inadequate" in decision.reasons


def test_empty_assessable_full_audit_without_anomalies_can_conclude_normal() -> None:
    decision = decide_image_recall(TASK_ID, ASSET, observation(), review())

    assert decision.findings == ()
    assert decision.inspection_result is InspectionResult.NORMAL
    assert decision.needs_review is False
    assert decision.coverage.model_dump() == {
        "sufficient_coverage": True,
        "sufficient_quality": True,
        "negative_audit_complete": True,
    }


@pytest.mark.parametrize(
    ("image_review", "review_error", "reason"),
    [
        (review(assessable=False), None, "image_not_assessable"),
        (review(audited=False), None, "full_image_audit_incomplete"),
        (
            review(CandidateReview(index=0, verdict="accept", reason="no target")),
            None,
            "candidate_review_unmatched",
        ),
        (None, "api_timeout", "review_failed"),
    ],
)
def test_empty_image_needs_complete_matching_audit_to_conclude_normal(
    image_review: ImageReview | None,
    review_error: str | None,
    reason: str,
) -> None:
    decision = decide_image_recall(
        TASK_ID,
        ASSET,
        observation(),
        image_review,
        review_error=review_error,
    )

    assert decision.inspection_result is InspectionResult.INCONCLUSIVE
    assert decision.needs_review is True
    assert reason in decision.reasons


def test_original_prefix_is_immutable_and_only_exact_vlm_duplicate_is_folded() -> None:
    first = detection(DetectionLabel.AJ, 0.91, (2, 3, 20, 30))
    second = detection(DetectionLabel.PL, 0.67, (24, 8, 50, 36))
    raw_review = review(
        CandidateReview(index=0, verdict="accept", reason="same AJ visible"),
        CandidateReview(
            index=1,
            verdict="revise",
            proposed_label=DetectionLabel.BX,
            reason="looks deformed",
        ),
        additional=(
            AdditionalAnomaly(
                label=DetectionLabel.AJ,
                name="duplicate AJ",
                bbox_xyxy=first.bbox_xyxy,
                reason="same exact evidence",
            ),
            AdditionalAnomaly(
                label=DetectionLabel.PL,
                name="nearby crack",
                bbox_xyxy=(25, 8, 50, 36),
                reason="distinct nearby evidence",
            ),
        ),
    )
    original_review = raw_review.model_dump()

    decision = decide_image_recall(
        TASK_ID,
        ASSET,
        observation(first, second),
        raw_review,
    )

    assert len(decision.findings) == 3
    for finding, original in zip(decision.findings[:2], (first, second), strict=True):
        assert finding.source is FindingSource.DETECTOR
        assert finding.canonical_label == original.label
        assert finding.score == original.score
        assert finding.evidence[0].region.bbox_xyxy == original.bbox_xyxy
    assert decision.findings[0].review_status is ReviewStatus.CONFIRMED
    assert decision.findings[1].review_status is ReviewStatus.NEEDS_REVIEW
    added = decision.findings[2]
    assert added.source is FindingSource.VLM
    assert added.canonical_label is DetectionLabel.PL
    assert added.evidence[0].region.bbox_xyxy == (25, 8, 50, 36)
    assert added.review_status is ReviewStatus.NEEDS_REVIEW
    assert decision.inspection_result is InspectionResult.DEFECTS_FOUND
    assert decision.needs_review is True
    assert raw_review.model_dump() == original_review


def test_conflicting_duplicate_candidate_reviews_cannot_confirm() -> None:
    decision = decide_image_recall(
        TASK_ID,
        ASSET,
        observation(detection(DetectionLabel.AJ, 0.95)),
        review(
            CandidateReview(index=0, verdict="reject", reason="not visible"),
            CandidateReview(index=0, verdict="accept", reason="visible"),
        ),
    )

    assert decision.findings[0].review_status is ReviewStatus.NEEDS_REVIEW
    assert decision.inspection_result is InspectionResult.INCONCLUSIVE
    assert "candidate_review_ambiguous" in decision.reasons
