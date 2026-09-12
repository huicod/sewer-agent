"""Supplemental enhancement evidence never replaces baseline findings."""

from uuid import NAMESPACE_URL, UUID, uuid5

from pipe_inspector.agent.enhancement_review import validate_enhancement_review
from pipe_inspector.contracts.common import ImageRegion
from pipe_inspector.contracts.enhancement import EnhancementResult, EnhancementReview
from pipe_inspector.contracts.findings import EvidenceRef, Finding
from pipe_inspector.contracts.inspection import InspectionDecision


def apply_enhancement_review(
    baseline: InspectionDecision,
    result: EnhancementResult | None,
    review: EnhancementReview | None,
    *,
    task_id: UUID | None = None,
    error: str | None = None,
) -> InspectionDecision:
    findings = list(baseline.findings)
    reasons = list(baseline.reasons)
    if error or result is None or review is None:
        reasons.append(error or "enhancement_review_missing")
    else:
        validate_enhancement_review(review, result, baseline)
        conflicts = set(review.conflicting_finding_ids)
        findings = [
            Finding.model_validate(
                finding.model_dump()
                | {
                    "review_status": "needs_review",
                    "confirmed_by": None,
                }
            )
            if finding.finding_id in conflicts
            else finding
            for finding in findings
        ]
        if conflicts:
            reasons.append("enhancement_conflict")
        if review.additional_anomalies:
            reasons.append("enhancement_additional_anomalies")
        owner = task_id or (findings[0].task_id if findings else None)
        for index, anomaly in enumerate(review.additional_anomalies):
            if owner is None:
                raise ValueError("task_id is required for additional findings")
            region = (
                ImageRegion(
                    width=result.source_region.width,
                    height=result.source_region.height,
                    bbox_xyxy=anomaly.bbox_xyxy,
                )
                if anomaly.bbox_xyxy is not None
                else None
            )
            findings.append(
                Finding(
                    finding_id=uuid5(NAMESPACE_URL, f"{owner}:enhancement:{index}"),
                    task_id=owner,
                    canonical_label=anomaly.label,
                    open_label=anomaly.name if anomaly.name else None,
                    source="vlm",
                    review_status="needs_review",
                    evidence=(EvidenceRef(asset=result.input_asset, region=region),),
                )
            )
    unresolved = any(f.review_status in {"tentative", "needs_review"} for f in findings)
    confirmed = any(f.review_status == "confirmed" for f in findings)
    needs_review = (
        baseline.needs_review or unresolved or bool(error) or review is None or result is None
    )
    conclusion = (
        "defects_found"
        if confirmed
        else "inconclusive"
        if needs_review
        else baseline.inspection_result
    )
    return InspectionDecision(
        findings=tuple(findings),
        inspection_result=conclusion,
        coverage=baseline.coverage,
        needs_review=needs_review,
        reasons=tuple(dict.fromkeys(reasons)),
    )
