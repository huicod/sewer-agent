"""Additive-only fusion policy for conditional open-detector candidates."""

from collections.abc import Sequence
from uuid import UUID, uuid5

from pipe_inspector.contracts.common import ImageRegion
from pipe_inspector.contracts.findings import (
    EvidenceRef,
    Finding,
    FindingSource,
    ReviewStatus,
)
from pipe_inspector.contracts.inspection import InspectionDecision
from pipe_inspector.contracts.open_scan import OpenCandidate, OpenScanEvidence
from pipe_inspector.contracts.tasks import CoverageSummary, InspectionResult

OPEN_CANDIDATE_REVIEW_REASON = "open_candidate_requires_review"
OPEN_SCAN_INCOMPLETE_REASON = "open_scan_incomplete"


def _intersection_over_union(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> float:
    left = max(first[0], second[0])
    top = max(first[1], second[1])
    right = min(first[2], second[2])
    bottom = min(first[3], second[3])
    intersection = max(0.0, right - left) * max(0.0, bottom - top)
    if intersection == 0:
        return 0.0
    first_area = (first[2] - first[0]) * (first[3] - first[1])
    second_area = (second[2] - second[0]) * (second[3] - second[1])
    return intersection / (first_area + second_area - intersection)


def _is_duplicate(
    candidate: OpenCandidate,
    scan: OpenScanEvidence,
    findings: Sequence[Finding],
) -> bool:
    for finding in findings:
        if (
            finding.review_status is ReviewStatus.REJECTED
            or finding.canonical_label != candidate.label
            or (candidate.label == "OTHER" and finding.open_label != candidate.name)
        ):
            continue
        for evidence in finding.evidence:
            if (
                evidence.asset == scan.asset
                and evidence.frame_index == scan.frame_index
                and evidence.region is not None
                and _intersection_over_union(evidence.region.bbox_xyxy, candidate.bbox_xyxy)
                >= 0.5
            ):
                return True
    return False


def _stable_finding_id(
    task_id: UUID,
    scan: OpenScanEvidence,
    candidate: OpenCandidate,
    occupied_ids: set[UUID],
) -> UUID:
    label = candidate.label.value if hasattr(candidate.label, "value") else candidate.label
    coordinates = ",".join(float(value).hex() for value in candidate.bbox_xyxy)
    identity = ":".join(
        (
            "open-candidate",
            scan.batch.detector_id,
            scan.batch.model_version,
            scan.batch.vocabulary_version,
            str(scan.asset.asset_id),
            str(scan.asset.version),
            "image" if scan.frame_index is None else str(scan.frame_index),
            label,
            candidate.name,
            coordinates,
        )
    )
    finding_id = uuid5(task_id, identity)
    occurrence = 1
    while finding_id in occupied_ids:
        finding_id = uuid5(task_id, f"{identity}:occurrence:{occurrence}")
        occurrence += 1
    return finding_id


def _finding(
    task_id: UUID,
    scan: OpenScanEvidence,
    candidate: OpenCandidate,
    occupied_ids: set[UUID],
) -> Finding:
    return Finding(
        finding_id=_stable_finding_id(task_id, scan, candidate, occupied_ids),
        task_id=task_id,
        canonical_label=candidate.label,
        open_label=candidate.name if candidate.label == "OTHER" else None,
        review_status=ReviewStatus.NEEDS_REVIEW,
        source=FindingSource.OPEN_DETECTOR,
        score=candidate.score,
        evidence=(
            EvidenceRef(
                asset=scan.asset,
                region=ImageRegion(
                    width=scan.batch.width,
                    height=scan.batch.height,
                    bbox_xyxy=candidate.bbox_xyxy,
                ),
                frame_index=scan.frame_index,
                timestamp_seconds=scan.timestamp_seconds,
            ),
        ),
    )


def _add_reason(reasons: list[str], reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)


def merge_open_candidates(
    decision: InspectionDecision,
    *,
    task_id: UUID,
    scans: Sequence[OpenScanEvidence],
    incomplete: bool = False,
) -> InspectionDecision:
    """Append distinct open candidates without changing any prior finding."""

    findings = list(decision.findings)
    occupied_ids = {finding.finding_id for finding in findings}
    added = False
    for scan in scans:
        for candidate in scan.batch.candidates:
            if _is_duplicate(candidate, scan, findings):
                continue
            new_finding = _finding(task_id, scan, candidate, occupied_ids)
            findings.append(new_finding)
            occupied_ids.add(new_finding.finding_id)
            added = True

    if not added and not incomplete:
        return decision

    reasons = list(decision.reasons)
    if added:
        _add_reason(reasons, OPEN_CANDIDATE_REVIEW_REASON)
    if incomplete:
        _add_reason(reasons, OPEN_SCAN_INCOMPLETE_REASON)

    coverage = decision.coverage
    if incomplete:
        coverage = CoverageSummary(
            sufficient_coverage=decision.coverage.sufficient_coverage,
            sufficient_quality=decision.coverage.sufficient_quality,
            negative_audit_complete=False,
        )

    inspection_result = decision.inspection_result
    if inspection_result is InspectionResult.NORMAL:
        inspection_result = InspectionResult.INCONCLUSIVE

    return InspectionDecision(
        findings=tuple(findings),
        inspection_result=inspection_result,
        coverage=coverage,
        needs_review=decision.needs_review or added or incomplete,
        reasons=tuple(reasons),
    )
