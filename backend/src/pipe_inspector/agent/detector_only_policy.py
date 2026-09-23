"""Deterministic findings for image runs that explicitly skip VLM review."""

from uuid import UUID, uuid4

from pipe_inspector.contracts.assets import AssetRef
from pipe_inspector.contracts.common import ImageRegion
from pipe_inspector.contracts.findings import EvidenceRef, Finding, FindingSource, ReviewStatus
from pipe_inspector.contracts.inspection import ImageObservation, InspectionDecision
from pipe_inspector.contracts.tasks import CoverageSummary, InspectionResult

DETECTOR_ONLY_POLICY_VERSION = "detector-only-v1"


def decide_detector_only(
    task_id: UUID,
    asset: AssetRef,
    observation: ImageObservation,
) -> InspectionDecision:
    """Keep raw detections and conclude without claiming a negative image audit."""
    adequate = observation.quality.adequate
    findings = tuple(
        Finding(
            finding_id=uuid4(),
            task_id=task_id,
            canonical_label=detection.label,
            review_status=(ReviewStatus.CONFIRMED if adequate else ReviewStatus.NEEDS_REVIEW),
            source=FindingSource.DETECTOR,
            score=detection.score,
            evidence=(
                EvidenceRef(
                    asset=asset,
                    region=ImageRegion(
                        width=observation.detection.width,
                        height=observation.detection.height,
                        bbox_xyxy=detection.bbox_xyxy,
                    ),
                ),
            ),
            confirmed_by="detector_only_rules" if adequate else None,
        )
        for detection in observation.detection.detections
    )
    reasons = ["vlm_review_skipped_by_mode"]
    if not findings:
        reasons.append("no_five_class_detections")
    if not adequate:
        reasons.append("quality_inadequate")
    needs_review = bool(findings and not adequate)
    result = (
        InspectionResult.DEFECTS_FOUND
        if findings and adequate
        else InspectionResult.INCONCLUSIVE
    )
    return InspectionDecision(
        findings=findings,
        inspection_result=result,
        coverage=CoverageSummary(
            sufficient_coverage=True,
            sufficient_quality=adequate,
            negative_audit_complete=False,
        ),
        needs_review=needs_review,
        reasons=tuple(reasons),
    )
