"""Recall-preserving fusion policy for still-image detector and VLM evidence."""

from __future__ import annotations

from uuid import UUID

from pipe_inspector.agent.image_policy import decide_image
from pipe_inspector.contracts.assets import AssetRef
from pipe_inspector.contracts.findings import Finding, ReviewStatus
from pipe_inspector.contracts.inspection import (
    AdditionalAnomaly,
    CandidateReview,
    ImageObservation,
    ImageReview,
    InspectionDecision,
)
from pipe_inspector.contracts.tasks import InspectionResult

RECALL_POLICY_VERSION = "image-recall-v1"


def _pending(finding: Finding) -> Finding:
    return Finding(
        finding_id=finding.finding_id,
        task_id=finding.task_id,
        canonical_label=finding.canonical_label,
        open_label=finding.open_label,
        review_status=ReviewStatus.NEEDS_REVIEW,
        source=finding.source,
        score=finding.score,
        evidence=finding.evidence,
        confirmed_by=None,
    )


def _is_exact_detector_duplicate(
    anomaly: AdditionalAnomaly,
    observation: ImageObservation,
) -> bool:
    if anomaly.bbox_xyxy is None:
        return False
    return any(
        anomaly.label == detection.label and anomaly.bbox_xyxy == detection.bbox_xyxy
        for detection in observation.detection.detections
    )


def _protect_disagreements(
    review: ImageReview,
    observation: ImageObservation,
) -> tuple[ImageReview, bool, bool, bool, bool]:
    rejected = False
    revised = False
    unmatched = False
    ambiguous = False
    candidates: list[CandidateReview] = []
    counts: dict[int, int] = {}
    for candidate in review.candidates:
        counts[candidate.index] = counts.get(candidate.index, 0) + 1
    handled_indices: set[int] = set()
    for candidate in review.candidates:
        original_label = None
        if candidate.index < len(observation.detection.detections):
            original_label = observation.detection.detections[candidate.index].label
        else:
            unmatched = True
        if counts[candidate.index] > 1:
            rejected = rejected or candidate.verdict == "reject"
            revised = (
                revised
                or candidate.verdict == "revise"
                or (
                    candidate.verdict == "accept"
                    and candidate.proposed_label is not None
                    and candidate.proposed_label != original_label
                )
            )
            ambiguous = True
            if candidate.index not in handled_indices:
                handled_indices.add(candidate.index)
                candidates.append(
                    CandidateReview(
                        index=candidate.index,
                        verdict="abstain",
                        reason="ambiguous duplicate candidate reviews",
                    )
                )
            continue
        is_rejection = candidate.verdict == "reject"
        is_revision = candidate.verdict == "revise" or (
            candidate.verdict == "accept"
            and candidate.proposed_label is not None
            and candidate.proposed_label != original_label
        )
        if is_rejection or is_revision:
            rejected = rejected or is_rejection
            revised = revised or is_revision
            candidates.append(
                CandidateReview(
                    index=candidate.index,
                    verdict="abstain",
                    reason=candidate.reason,
                )
            )
        else:
            candidates.append(candidate)
    return (
        ImageReview(
            image_assessable=review.image_assessable,
            full_image_audited=review.full_image_audited,
            candidates=tuple(candidates),
            additional_anomalies=tuple(
                anomaly
                for anomaly in review.additional_anomalies
                if not _is_exact_detector_duplicate(anomaly, observation)
            ),
            summary=review.summary,
        ),
        rejected,
        revised,
        unmatched,
        ambiguous,
    )


def decide_image_recall(
    task_id: UUID,
    asset: AssetRef,
    observation: ImageObservation,
    review: ImageReview | None,
    review_error: str | None = None,
) -> InspectionDecision:
    """Fuse image evidence without allowing a VLM to erase detector recall."""

    protected_review = review
    rejected = False
    revised = False
    unmatched = False
    ambiguous = False
    if review is not None and review_error is None:
        (
            protected_review,
            rejected,
            revised,
            unmatched,
            ambiguous,
        ) = _protect_disagreements(review, observation)

    decision = decide_image(
        task_id,
        asset,
        observation,
        protected_review,
        review_error=review_error,
    )
    reasons = list(decision.reasons)
    if review is None and review_error is None and "required_review_missing" not in reasons:
        reasons.append("required_review_missing")
    if rejected and "candidate_rejection_suggested" not in reasons:
        reasons.append("candidate_rejection_suggested")
    if revised and "label_revision_suggested" not in reasons:
        reasons.append("label_revision_suggested")
    if unmatched and "candidate_review_unmatched" not in reasons:
        reasons.append("candidate_review_unmatched")
    if ambiguous and "candidate_review_ambiguous" not in reasons:
        reasons.append("candidate_review_ambiguous")

    accepted_indices: set[int] = set()
    if (
        protected_review is not None
        and review_error is None
        and protected_review.image_assessable
        and observation.quality.adequate
    ):
        accepted_indices = {
            candidate.index
            for candidate in protected_review.candidates
            if candidate.verdict == "accept"
        }
    detector_count = len(observation.detection.detections)
    findings = tuple(
        _pending(finding)
        if index < detector_count
        and finding.review_status is ReviewStatus.CONFIRMED
        and index not in accepted_indices
        else finding
        for index, finding in enumerate(decision.findings)
    )
    unresolved = any(
        finding.review_status in {ReviewStatus.TENTATIVE, ReviewStatus.NEEDS_REVIEW}
        for finding in findings
    )
    confirmed = any(finding.review_status is ReviewStatus.CONFIRMED for finding in findings)
    blockers = bool(reasons) or unresolved
    if confirmed:
        inspection_result = InspectionResult.DEFECTS_FOUND
    elif (
        not blockers
        and decision.coverage.sufficient_quality
        and decision.coverage.negative_audit_complete
    ):
        inspection_result = InspectionResult.NORMAL
    else:
        inspection_result = InspectionResult.INCONCLUSIVE
    return InspectionDecision(
        findings=findings,
        inspection_result=inspection_result,
        coverage=decision.coverage,
        needs_review=blockers,
        reasons=tuple(reasons),
    )
