"""Deterministic evidence policy for a completed still-image observation."""

from __future__ import annotations

from uuid import UUID, uuid4

from pipe_inspector.agent.image_review import review_required
from pipe_inspector.contracts.assets import AssetRef
from pipe_inspector.contracts.common import ImageRegion
from pipe_inspector.contracts.findings import (
    EvidenceRef,
    Finding,
    FindingSource,
    ReviewStatus,
)
from pipe_inspector.contracts.inspection import (
    AdditionalAnomaly,
    CandidateReview,
    ImageObservation,
    ImageReview,
    InspectionDecision,
)
from pipe_inspector.contracts.tasks import CoverageSummary, InspectionResult

STRONG_EVIDENCE_SCORE = 0.85


def _add_reason(reasons: list[str], reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)


def _evidence(
    asset: AssetRef,
    observation: ImageObservation,
    bbox_xyxy: tuple[float, float, float, float] | None,
) -> tuple[EvidenceRef, ...]:
    region = None
    if bbox_xyxy is not None:
        region = ImageRegion(
            width=observation.detection.width,
            height=observation.detection.height,
            bbox_xyxy=bbox_xyxy,
        )
    return (EvidenceRef(asset=asset, region=region),)


def _candidate_finding(
    task_id: UUID,
    asset: AssetRef,
    observation: ImageObservation,
    index: int,
    candidate: CandidateReview | None,
    review_available: bool,
    reasons: list[str],
) -> Finding:
    detection = observation.detection.detections[index]
    label = detection.label
    status = ReviewStatus.NEEDS_REVIEW
    confirmed_by = None

    if not review_available or candidate is None:
        _add_reason(reasons, "candidate_review_missing")
    elif candidate.verdict == "accept":
        if candidate.proposed_label is not None and candidate.proposed_label != detection.label:
            label = candidate.proposed_label
            _add_reason(reasons, "label_revision_suggested")
        elif observation.quality.adequate:
            status = ReviewStatus.CONFIRMED
            confirmed_by = "rules"
        else:
            _add_reason(reasons, "quality_inadequate")
    elif candidate.verdict == "reject":
        if detection.score >= STRONG_EVIDENCE_SCORE:
            _add_reason(reasons, "strong_evidence_conflict")
        else:
            status = ReviewStatus.REJECTED
    elif candidate.verdict == "revise":
        if candidate.proposed_label is not None:
            label = candidate.proposed_label
        _add_reason(reasons, "label_revision_suggested")
    elif candidate.verdict == "abstain":
        _add_reason(reasons, "candidate_review_abstained")
    else:
        _add_reason(reasons, "candidate_needs_more_context")

    return Finding(
        finding_id=uuid4(),
        task_id=task_id,
        canonical_label=label,
        review_status=status,
        source=FindingSource.DETECTOR,
        score=detection.score,
        evidence=_evidence(asset, observation, detection.bbox_xyxy),
        confirmed_by=confirmed_by,
    )


def _additional_finding(
    task_id: UUID,
    asset: AssetRef,
    observation: ImageObservation,
    anomaly: AdditionalAnomaly,
) -> Finding:
    return Finding(
        finding_id=uuid4(),
        task_id=task_id,
        canonical_label=anomaly.label,
        open_label=anomaly.name,
        review_status=ReviewStatus.NEEDS_REVIEW,
        source=FindingSource.VLM,
        evidence=_evidence(asset, observation, anomaly.bbox_xyxy),
    )


def decide_image(
    task_id: UUID,
    asset: AssetRef,
    observation: ImageObservation,
    review: ImageReview | None,
    review_error: str | None = None,
) -> InspectionDecision:
    reasons: list[str] = []
    required = review_required(observation)
    effective_review = None if review_error is not None else review

    if review_error is not None:
        _add_reason(reasons, "review_failed")
    elif required and review is None:
        _add_reason(reasons, "required_review_missing")
    if not observation.quality.adequate:
        _add_reason(reasons, "quality_inadequate")
    if effective_review is not None and not effective_review.image_assessable:
        _add_reason(reasons, "image_not_assessable")
    if effective_review is not None and not effective_review.full_image_audited:
        _add_reason(reasons, "full_image_audit_incomplete")

    review_by_index: dict[int, CandidateReview] = {}
    if effective_review is not None:
        review_by_index = {candidate.index: candidate for candidate in effective_review.candidates}
    review_available = bool(effective_review is not None and effective_review.image_assessable)

    findings: list[Finding] = []
    for index in range(len(observation.detection.detections)):
        if not required and effective_review is None:
            detection = observation.detection.detections[index]
            findings.append(
                Finding(
                    finding_id=uuid4(),
                    task_id=task_id,
                    canonical_label=detection.label,
                    review_status=ReviewStatus.CONFIRMED,
                    source=FindingSource.DETECTOR,
                    score=detection.score,
                    evidence=_evidence(asset, observation, detection.bbox_xyxy),
                    confirmed_by="rules",
                )
            )
            continue
        findings.append(
            _candidate_finding(
                task_id,
                asset,
                observation,
                index,
                review_by_index.get(index),
                review_available,
                reasons,
            )
        )

    if effective_review is not None:
        for anomaly in effective_review.additional_anomalies:
            findings.append(_additional_finding(task_id, asset, observation, anomaly))
            _add_reason(reasons, "additional_anomaly_requires_review")

    sufficient_quality = bool(
        observation.quality.adequate
        and (effective_review is None or effective_review.image_assessable)
    )
    negative_audit_complete = bool(
        effective_review is not None
        and effective_review.image_assessable
        and effective_review.full_image_audited
    )
    coverage = CoverageSummary(
        sufficient_coverage=True,
        sufficient_quality=sufficient_quality,
        negative_audit_complete=negative_audit_complete,
    )

    unresolved = any(
        finding.review_status in {ReviewStatus.TENTATIVE, ReviewStatus.NEEDS_REVIEW}
        for finding in findings
    )
    confirmed = any(finding.review_status is ReviewStatus.CONFIRMED for finding in findings)
    blockers = bool(reasons) or unresolved
    if confirmed:
        inspection_result = InspectionResult.DEFECTS_FOUND
    elif not blockers and sufficient_quality and negative_audit_complete:
        inspection_result = InspectionResult.NORMAL
    else:
        inspection_result = InspectionResult.INCONCLUSIVE

    return InspectionDecision(
        findings=tuple(findings),
        inspection_result=inspection_result,
        coverage=coverage,
        needs_review=blockers,
        reasons=tuple(reasons),
    )
