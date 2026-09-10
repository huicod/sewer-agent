"""Deterministic review selection and evidence policy for video inspections."""

from __future__ import annotations

from uuid import UUID, uuid5

from pipe_inspector.agent.image_policy import decide_image
from pipe_inspector.agent.image_review import review_required
from pipe_inspector.contracts.assets import AssetRef
from pipe_inspector.contracts.common import ImageRegion
from pipe_inspector.contracts.findings import EvidenceRef, Finding, FindingSource, ReviewStatus
from pipe_inspector.contracts.inspection import ImageReview, InspectionDecision
from pipe_inspector.contracts.tasks import CoverageSummary, InspectionResult
from pipe_inspector.contracts.video import (
    EventEvidence,
    FrameObservation,
    VideoEvent,
    VideoScanResult,
)

EVENT_FRAME_BLOCKERS = frozenset(
    {
        "quality_inadequate",
        "image_not_assessable",
        "full_image_audit_incomplete",
        "review_failed",
        "required_review_missing",
    }
)


def select_review_frames(
    scan: VideoScanResult,
    observations: dict[int, FrameObservation],
    *,
    force_review: bool = False,
    budget: int = 32,
) -> tuple[int, ...]:
    """Select event evidence first, followed by mandatory periodic audit frames."""
    if budget < 0:
        raise ValueError("budget must be non-negative")

    selected: list[int] = []
    seen: set[int] = set()

    def add(frame_index: int) -> None:
        if frame_index not in seen:
            seen.add(frame_index)
            selected.append(frame_index)

    for event in scan.state.events:
        for item in event.evidence:
            observation = observations.get(item.frame_index)
            if observation is not None and (force_review or review_required(observation)):
                add(item.frame_index)
    for item in scan.state.audit_frames:
        add(item.frame_index)
    return tuple(selected[:budget])


def _add_reason(reasons: list[str], reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)


def _asset_for(scan: VideoScanResult, assets: dict[int, AssetRef], frame_index: int) -> AssetRef:
    return assets.get(frame_index, scan.state.source)


def _event_evidence_ref(
    scan: VideoScanResult,
    assets: dict[int, AssetRef],
    item: EventEvidence,
) -> EvidenceRef:
    return EvidenceRef(
        asset=_asset_for(scan, assets, item.frame_index),
        region=item.region,
        frame_index=item.frame_index,
        timestamp_seconds=item.timestamp_seconds,
    )


def _detection_index(
    event: VideoEvent,
    item: EventEvidence,
    observation: FrameObservation,
) -> int | None:
    detections = observation.detection.detections
    if item.detection_index is not None:
        if item.detection_index >= len(detections):
            return None
        return item.detection_index

    matches = [
        index
        for index, detection in enumerate(detections)
        if detection.label == event.label
        and detection.bbox_xyxy == item.region.bbox_xyxy
        and detection.score == item.score
    ]
    return matches[0] if len(matches) == 1 else None


def _event_finding(
    task_id: UUID,
    event: VideoEvent,
    scan: VideoScanResult,
    observations: dict[int, FrameObservation],
    assets: dict[int, AssetRef],
    reviews: dict[int, ImageReview],
    review_errors: dict[int, str],
    *,
    force_review: bool,
    reasons: list[str],
) -> Finding:
    statuses: list[ReviewStatus] = []
    for item in event.evidence:
        observation = observations.get(item.frame_index)
        if observation is None:
            _add_reason(reasons, "event_observation_missing")
            statuses.append(ReviewStatus.NEEDS_REVIEW)
            continue
        index = _detection_index(event, item, observation)
        if index is None:
            _add_reason(reasons, "event_detection_association_missing")
            statuses.append(ReviewStatus.NEEDS_REVIEW)
            continue

        required = force_review or review_required(observation)
        review = reviews.get(item.frame_index)
        review_error = review_errors.get(item.frame_index)
        if required and (review is None or review_error is not None):
            _add_reason(reasons, "event_review_missing")
            statuses.append(ReviewStatus.NEEDS_REVIEW)
            continue

        decision = decide_image(
            task_id,
            _asset_for(scan, assets, item.frame_index),
            observation,
            review,
            review_error=review_error,
        )
        for reason in decision.reasons:
            _add_reason(reasons, reason)
        candidate = decision.findings[index]
        if EVENT_FRAME_BLOCKERS.intersection(decision.reasons):
            statuses.append(ReviewStatus.NEEDS_REVIEW)
        elif (
            candidate.review_status is ReviewStatus.CONFIRMED
            and candidate.canonical_label == event.label
        ):
            statuses.append(ReviewStatus.CONFIRMED)
        elif candidate.review_status is ReviewStatus.REJECTED:
            statuses.append(ReviewStatus.REJECTED)
        else:
            _add_reason(reasons, "event_review_conflict")
            statuses.append(ReviewStatus.NEEDS_REVIEW)

    if statuses and all(status is ReviewStatus.CONFIRMED for status in statuses):
        status = ReviewStatus.CONFIRMED
        confirmed_by = "rules"
    elif statuses and all(status is ReviewStatus.REJECTED for status in statuses):
        status = ReviewStatus.REJECTED
        confirmed_by = None
    else:
        status = ReviewStatus.NEEDS_REVIEW
        confirmed_by = None
        _add_reason(reasons, "event_review_incomplete")

    return Finding(
        finding_id=uuid5(task_id, f"video-event:{event.event_id}"),
        task_id=task_id,
        canonical_label=event.label,
        review_status=status,
        source=FindingSource.DETECTOR,
        score=max(item.score for item in event.evidence),
        evidence=tuple(_event_evidence_ref(scan, assets, item) for item in event.evidence[:3]),
        confirmed_by=confirmed_by,
    )


def _additional_findings(
    task_id: UUID,
    scan: VideoScanResult,
    observations: dict[int, FrameObservation],
    assets: dict[int, AssetRef],
    reviews: dict[int, ImageReview],
    review_errors: dict[int, str],
) -> list[Finding]:
    findings: list[Finding] = []
    for frame_index in sorted(reviews):
        if frame_index in review_errors:
            continue
        observation = observations.get(frame_index)
        if observation is None:
            continue
        review = reviews[frame_index]
        for anomaly_index, anomaly in enumerate(review.additional_anomalies):
            region = None
            if anomaly.bbox_xyxy is not None:
                region = ImageRegion(
                    width=observation.detection.width,
                    height=observation.detection.height,
                    bbox_xyxy=anomaly.bbox_xyxy,
                )
            findings.append(
                Finding(
                    finding_id=uuid5(
                        task_id,
                        f"video-additional:{frame_index}:{anomaly_index}",
                    ),
                    task_id=task_id,
                    canonical_label=anomaly.label,
                    open_label=anomaly.name,
                    review_status=ReviewStatus.NEEDS_REVIEW,
                    source=FindingSource.VLM,
                    evidence=(
                        EvidenceRef(
                            asset=_asset_for(scan, assets, frame_index),
                            region=region,
                            frame_index=frame_index,
                            timestamp_seconds=observation.timestamp_seconds,
                        ),
                    ),
                )
            )
    return findings


def decide_video(
    task_id: UUID,
    scan: VideoScanResult,
    observations: dict[int, FrameObservation],
    assets: dict[int, AssetRef],
    reviews: dict[int, ImageReview],
    review_errors: dict[int, str],
    *,
    force_review: bool = False,
) -> InspectionDecision:
    """Resolve frame reviews into conservative event-level findings and coverage."""
    reasons: list[str] = []
    if not scan.decode_complete:
        _add_reason(reasons, "decode_incomplete")
    if scan.error_code is not None:
        _add_reason(reasons, "video_scan_failed")
    if scan.state.adequate_frames != scan.state.next_frame:
        _add_reason(reasons, "quality_inadequate")
    if review_errors:
        _add_reason(reasons, "review_failed")

    findings = [
        _event_finding(
            task_id,
            event,
            scan,
            observations,
            assets,
            reviews,
            review_errors,
            force_review=force_review,
            reasons=reasons,
        )
        for event in scan.state.events
    ]
    findings.extend(
        _additional_findings(task_id, scan, observations, assets, reviews, review_errors)
    )

    represented_detections: set[tuple[int, int]] = set()
    for event in scan.state.events:
        for item in event.evidence:
            observation = observations.get(item.frame_index)
            if observation is None:
                continue
            index = _detection_index(event, item, observation)
            if index is not None:
                represented_detections.add((item.frame_index, index))

    audit_complete = bool(scan.state.audit_frames)
    audit_unresolved = False
    for audit in scan.state.audit_frames:
        observation = observations.get(audit.frame_index)
        review = reviews.get(audit.frame_index)
        review_error = review_errors.get(audit.frame_index)
        if observation is None:
            audit_complete = False
            _add_reason(reasons, "audit_observation_missing")
            continue
        if review_error is not None:
            audit_complete = False
            _add_reason(reasons, "audit_review_failed")
            continue
        if review is None:
            audit_complete = False
            _add_reason(reasons, "audit_review_missing")
            continue
        if not review.image_assessable:
            audit_complete = False
            _add_reason(reasons, "audit_not_assessable")
            continue
        if not review.full_image_audited:
            audit_complete = False
            _add_reason(reasons, "audit_incomplete")
            continue
        audit_decision = decide_image(
            task_id,
            _asset_for(scan, assets, audit.frame_index),
            observation,
            review,
        )
        for index, finding in enumerate(audit_decision.findings):
            unresolved_candidate = finding.review_status in {
                ReviewStatus.TENTATIVE,
                ReviewStatus.NEEDS_REVIEW,
            }
            unmatched_confirmation = bool(
                finding.review_status is ReviewStatus.CONFIRMED
                and (audit.frame_index, index) not in represented_detections
            )
            if unresolved_candidate or unmatched_confirmation:
                audit_unresolved = True
                _add_reason(reasons, "audit_candidate_unresolved")
    if not scan.state.audit_frames:
        _add_reason(reasons, "audit_frames_missing")

    reviewed_assessable = not review_errors and all(
        review.image_assessable for review in reviews.values()
    )
    coverage = CoverageSummary(
        sufficient_coverage=scan.decode_complete,
        sufficient_quality=bool(
            scan.state.adequate_frames == scan.state.next_frame and reviewed_assessable
        ),
        negative_audit_complete=audit_complete,
    )
    unresolved = audit_unresolved or any(
        finding.review_status in {ReviewStatus.TENTATIVE, ReviewStatus.NEEDS_REVIEW}
        for finding in findings
    )
    confirmed = any(finding.review_status is ReviewStatus.CONFIRMED for finding in findings)
    needs_review = bool(reasons) or unresolved
    if confirmed:
        inspection_result = InspectionResult.DEFECTS_FOUND
    elif (
        not needs_review
        and coverage.sufficient_coverage
        and coverage.sufficient_quality
        and coverage.negative_audit_complete
    ):
        inspection_result = InspectionResult.NORMAL
    else:
        inspection_result = InspectionResult.INCONCLUSIVE

    return InspectionDecision(
        findings=tuple(findings),
        inspection_result=inspection_result,
        coverage=coverage,
        needs_review=needs_review,
        reasons=tuple(reasons),
    )
