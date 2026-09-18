"""Online decision adapter for conservative guided review."""

from __future__ import annotations

from uuid import UUID, uuid4

from pydantic import ValidationError

from pipe_inspector.agent.guided_fusion import fuse_guided
from pipe_inspector.agent.guided_review import GuidedReview
from pipe_inspector.contracts.assets import AssetRef
from pipe_inspector.contracts.common import ImageRegion
from pipe_inspector.contracts.findings import EvidenceRef, Finding, FindingSource, ReviewStatus
from pipe_inspector.contracts.inspection import ImageObservation, InspectionDecision
from pipe_inspector.contracts.tasks import CoverageSummary, InspectionResult


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


def _valid_review(review: GuidedReview, observation: ImageObservation) -> bool:
    indices = [candidate.index for candidate in review.candidates]
    if len(indices) != len(set(indices)) or sorted(indices) != list(
        range(len(observation.detection.detections))
    ):
        return False
    for candidate in review.candidates:
        original = observation.detection.detections[candidate.index].label
        proposed = candidate.proposed_label
        if (
            (candidate.verdict == "revise" and proposed in (None, original))
            or (candidate.verdict == "accept" and proposed not in (None, original))
            or (candidate.verdict == "accept" and candidate.original_presence == "absent")
            or (candidate.verdict == "reject" and candidate.original_presence == "present")
            or (candidate.verdict == "revise" and candidate.original_presence == "present")
            or (
                candidate.verdict in ("reject", "abstain", "need_more_context")
                and proposed is not None
            )
        ):
            return False
    for anomaly in review.additional_anomalies:
        if anomaly.bbox_xyxy is None:
            continue
        try:
            ImageRegion(
                width=observation.detection.width,
                height=observation.detection.height,
                bbox_xyxy=anomaly.bbox_xyxy,
            )
        except ValidationError:
            return False
    return True


def decide_image_guided(
    task_id: UUID,
    asset: AssetRef,
    observation: ImageObservation,
    review: GuidedReview | None,
    review_error: str | None = None,
) -> InspectionDecision:
    """Translate guided fusion actions into auditable online findings."""
    reasons: list[str] = []
    effective_review = review
    if review_error is not None:
        effective_review = None
        _add_reason(reasons, "review_failed")
    elif review is None:
        _add_reason(reasons, "required_review_missing")
    elif not _valid_review(review, observation):
        effective_review = None
        _add_reason(reasons, "review_invalid")

    if not observation.quality.adequate:
        _add_reason(reasons, "quality_inadequate")
    if effective_review is not None and not effective_review.image_assessable:
        _add_reason(reasons, "image_not_assessable")
    if effective_review is not None and not effective_review.full_image_audited:
        _add_reason(reasons, "full_image_audit_incomplete")

    fused = fuse_guided(observation, effective_review)
    findings: list[Finding] = []
    raw_count = len(observation.detection.detections)
    for change in fused["changes"][:raw_count]:
        detection = observation.detection.detections[change["index"]]
        action = change["action"]
        status = ReviewStatus.NEEDS_REVIEW
        confirmed_by = None
        if action == "reject":
            status = ReviewStatus.REJECTED
        elif action in {"accept", "revise"} and observation.quality.adequate:
            status = ReviewStatus.CONFIRMED
            confirmed_by = "rules"
        else:
            _add_reason(reasons, "guided_advice_requires_review")
        findings.append(
            Finding(
                finding_id=uuid4(),
                task_id=task_id,
                canonical_label=change["label"],
                review_status=status,
                source=FindingSource.DETECTOR,
                score=detection.score,
                evidence=_evidence(asset, observation, detection.bbox_xyxy),
                confirmed_by=confirmed_by,
            )
        )

    if effective_review is not None:
        for change in fused["changes"][raw_count:]:
            anomaly = effective_review.additional_anomalies[change["additional_index"]]
            confirmed = change["action"] == "add" and observation.quality.adequate
            status = ReviewStatus.CONFIRMED if confirmed else ReviewStatus.NEEDS_REVIEW
            if not confirmed:
                _add_reason(reasons, "additional_anomaly_requires_review")
            findings.append(
                Finding(
                    finding_id=uuid4(),
                    task_id=task_id,
                    canonical_label=anomaly.label,
                    open_label=anomaly.name,
                    review_status=status,
                    source=FindingSource.VLM,
                    score=None,
                    evidence=_evidence(asset, observation, anomaly.bbox_xyxy),
                    confirmed_by="rules" if confirmed else None,
                )
            )

    sufficient_quality = bool(
        observation.quality.adequate
        and effective_review is not None
        and effective_review.image_assessable
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
    unresolved = any(item.review_status is ReviewStatus.NEEDS_REVIEW for item in findings)
    confirmed = any(item.review_status is ReviewStatus.CONFIRMED for item in findings)
    blockers = bool(reasons) or unresolved
    if confirmed:
        result = InspectionResult.DEFECTS_FOUND
    elif not blockers and sufficient_quality and negative_audit_complete:
        result = InspectionResult.NORMAL
    else:
        result = InspectionResult.INCONCLUSIVE
    return InspectionDecision(
        findings=tuple(findings),
        inspection_result=result,
        coverage=coverage,
        needs_review=blockers,
        reasons=tuple(reasons),
    )
