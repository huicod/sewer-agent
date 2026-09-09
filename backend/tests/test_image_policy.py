from uuid import UUID

import pytest
from pipe_inspector.agent.image_policy import decide_image
from pipe_inspector.agent.image_review import review_required
from pipe_inspector.contracts.assets import AssetRef
from pipe_inspector.contracts.detection import Detection, DetectionBatch, DetectionLabel
from pipe_inspector.contracts.findings import ReviewStatus
from pipe_inspector.contracts.inspection import (
    AdditionalAnomaly,
    CandidateReview,
    ImageObservation,
    ImageReview,
    QualitySummary,
)
from pipe_inspector.contracts.tasks import InspectionResult

TASK_ID = UUID("00000000-0000-4000-8000-000000000001")
ASSET = AssetRef(asset_id=UUID("00000000-0000-4000-8000-000000000002"))


def detection(label: DetectionLabel = DetectionLabel.AJ, score: float = 0.95) -> Detection:
    return Detection(label=label, native_label=label.value, score=score, bbox_xyxy=(2, 3, 20, 30))


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
        summary="结构化复核完成",
    )


@pytest.mark.parametrize("label", [DetectionLabel.FS, DetectionLabel.BX])
def test_weak_classes_always_require_review(label) -> None:
    assert review_required(observation(detection(label, 0.99))) is True


@pytest.mark.parametrize("label", [DetectionLabel.AJ, DetectionLabel.PL, DetectionLabel.BLOCK])
def test_adequate_strong_other_trained_classes_can_skip_review(label) -> None:
    assert review_required(observation(detection(label, 0.85))) is False


def test_zero_low_score_and_inadequate_observations_require_review() -> None:
    assert review_required(observation()) is True
    assert review_required(observation(detection(score=0.849))) is True
    assert review_required(observation(detection(), adequate=False)) is True


def test_completed_zero_detection_audit_can_conclude_normal() -> None:
    decision = decide_image(TASK_ID, ASSET, observation(), review())

    assert decision.inspection_result is InspectionResult.NORMAL
    assert decision.findings == ()
    assert decision.needs_review is False
    assert decision.coverage.model_dump() == {
        "sufficient_coverage": True,
        "sufficient_quality": True,
        "negative_audit_complete": True,
    }


def test_strong_rejection_conflict_stays_for_human_review() -> None:
    decision = decide_image(
        TASK_ID,
        ASSET,
        observation(detection(score=0.95)),
        review(CandidateReview(index=0, verdict="reject", reason="VLM 未见缺陷")),
    )

    assert decision.inspection_result is InspectionResult.INCONCLUSIVE
    assert decision.needs_review is True
    assert decision.findings[0].review_status is ReviewStatus.NEEDS_REVIEW
    assert "strong_evidence_conflict" in decision.reasons


def test_weak_rejected_candidate_with_full_audit_can_conclude_normal() -> None:
    decision = decide_image(
        TASK_ID,
        ASSET,
        observation(detection(score=0.5)),
        review(CandidateReview(index=0, verdict="reject", reason="确认是假阳性")),
    )

    assert decision.inspection_result is InspectionResult.NORMAL
    assert decision.findings[0].review_status is ReviewStatus.REJECTED
    assert decision.coverage.negative_audit_complete is True


def test_accepting_original_label_confirms_finding_by_rules() -> None:
    decision = decide_image(
        TASK_ID,
        ASSET,
        observation(detection(DetectionLabel.FS, 0.9)),
        review(CandidateReview(index=0, verdict="accept", reason="检测与复核一致")),
    )

    finding = decision.findings[0]
    assert decision.inspection_result is InspectionResult.DEFECTS_FOUND
    assert decision.needs_review is False
    assert finding.review_status is ReviewStatus.CONFIRMED
    assert finding.confirmed_by == "rules"
    assert finding.finding_id.version == 4


def test_revised_label_is_advice_for_human_not_auto_confirmation() -> None:
    decision = decide_image(
        TASK_ID,
        ASSET,
        observation(detection(DetectionLabel.FS, 0.9)),
        review(
            CandidateReview(
                index=0,
                verdict="revise",
                proposed_label="BX",
                reason="建议改判为变形",
            )
        ),
    )

    assert decision.needs_review is True
    assert decision.findings[0].canonical_label is DetectionLabel.BX
    assert decision.findings[0].review_status is ReviewStatus.NEEDS_REVIEW


def test_additional_other_anomaly_is_never_auto_confirmed() -> None:
    anomaly = AdditionalAnomaly(
        label="OTHER",
        name="疑似树根",
        bbox_xyxy=(4, 5, 16, 18),
        reason="不属于五类标签",
    )
    decision = decide_image(TASK_ID, ASSET, observation(), review(additional=(anomaly,)))

    finding = decision.findings[0]
    assert decision.needs_review is True
    assert decision.inspection_result is InspectionResult.INCONCLUSIVE
    assert finding.canonical_label == "OTHER"
    assert finding.open_label == "疑似树根"
    assert finding.review_status is ReviewStatus.NEEDS_REVIEW
    assert finding.confirmed_by is None


@pytest.mark.parametrize(
    ("image_review", "review_error", "reason"),
    [
        (None, None, "required_review_missing"),
        (None, "timeout", "review_failed"),
        (review(assessable=False), None, "image_not_assessable"),
        (review(audited=False), None, "full_image_audit_incomplete"),
    ],
)
def test_missing_failed_or_incomplete_review_never_concludes_normal(
    image_review, review_error, reason
) -> None:
    decision = decide_image(
        TASK_ID,
        ASSET,
        observation(),
        image_review,
        review_error=review_error,
    )

    assert decision.inspection_result is InspectionResult.INCONCLUSIVE
    assert decision.needs_review is True
    assert reason in decision.reasons


def test_inadequate_quality_blocks_normal_even_after_full_audit() -> None:
    decision = decide_image(TASK_ID, ASSET, observation(adequate=False), review())

    assert decision.inspection_result is InspectionResult.INCONCLUSIVE
    assert decision.coverage.sufficient_quality is False
    assert decision.needs_review is True
    assert "quality_inadequate" in decision.reasons
