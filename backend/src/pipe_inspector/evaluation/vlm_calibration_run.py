"""Small validation-only helpers for the guided VLM calibration run.

This module deliberately does not change the online conservative fusion policy.
The experimental addition projection is used only to measure whether a future
calibrated promotion could help on validation data.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pipe_inspector.agent.guided_fusion import NOVELTY_IOU
from pipe_inspector.agent.guided_review import GuidedReview
from pipe_inspector.contracts.detection import Detection, DetectionBatch, DetectionLabel
from pipe_inspector.contracts.inspection import ImageObservation, QualitySummary
from pipe_inspector.evaluation.review import LabeledBox


def reconstruct_observation(
    width: int,
    height: int,
    candidates: Sequence[Mapping[str, Any]],
    *,
    quality: QualitySummary | None = None,
) -> ImageObservation:
    """Rebuild a typed observation from an immutable detector export."""
    detections = []
    for item in candidates:
        x1, y1, x2, y2 = (float(value) for value in item["bbox_xyxy"])
        box = (
            max(0.0, min(float(width), x1)),
            max(0.0, min(float(height), y1)),
            max(0.0, min(float(width), x2)),
            max(0.0, min(float(height), y2)),
        )
        if box[2] <= box[0] or box[3] <= box[1]:
            continue
        detections.append(
            Detection(
                label=str(item["label"]),
                native_label=str(item["label"]),
                score=float(item.get("score", 1.0)),
                bbox_xyxy=box,
            )
        )
    detections = tuple(detections)
    return ImageObservation(
        detection=DetectionBatch(
            detector_id="ultralytics-yolo26m-validation-cache",
            model_version="merged_five_yolo26m_960_5090/best",
            label_space_version="sewer2403-5class-v1",
            width=width,
            height=height,
            detections=detections,
        ),
        quality=quality
        or QualitySummary(
            mean_luminance=100,
            contrast=30,
            sharpness=80,
            adequate=True,
            reasons=(),
        ),
    )


def filter_candidates(
    candidates: Sequence[Mapping[str, Any]], *, min_score: float = 0.25
) -> list[Mapping[str, Any]]:
    """Match the production detector operating point, not mAP's low floor."""
    return [item for item in candidates if float(item.get("score", 1.0)) >= min_score]


def independent_confirmation_accepts(review: GuidedReview, label: str) -> bool:
    """Accept a synthetic one-candidate second review only on explicit evidence.

    The caller must construct the review from a separate provider request.  This
    helper intentionally ignores additional anomalies from that request: the
    experiment is confirming one previously proposed addition, not recursively
    opening another addition channel.
    """
    if len(review.candidates) != 1:
        return False
    candidate = review.candidates[0]
    return (
        candidate.verdict == "accept"
        and candidate.original_presence == "present"
        and candidate.evidence_strength == "clear"
        and candidate.proposed_label in (None, label)
    )


def _iou(left: Sequence[float], right: Sequence[float]) -> float:
    lx1, ly1, lx2, ly2 = left
    rx1, ry1, rx2, ry2 = right
    intersection = max(0.0, min(lx2, rx2) - max(lx1, rx1)) * max(
        0.0, min(ly2, ry2) - max(ly1, ry1)
    )
    if intersection == 0:
        return 0.0
    left_area = (lx2 - lx1) * (ly2 - ly1)
    right_area = (rx2 - rx1) * (ry2 - ry1)
    return intersection / (left_area + right_area - intersection)


def experimental_addition_boxes(
    observation: ImageObservation,
    review: GuidedReview,
) -> list[LabeledBox]:
    """Project only strictly confirmed, non-overlapping five-class additions.

    This is intentionally an offline diagnostic projection. The online
    conservative policy still leaves every VLM-only addition pending.
    """
    if not review.image_assessable:
        return []
    detector_boxes = [item.bbox_xyxy for item in observation.detection.detections]
    accepted: list[LabeledBox] = []
    for anomaly in review.additional_anomalies:
        if (
            anomaly.label not in tuple(DetectionLabel)
            or anomaly.bbox_xyxy is None
            or anomaly.evidence_strength != "clear"
            or anomaly.confirmation_status != "confirmed"
        ):
            continue
        if any(_iou(anomaly.bbox_xyxy, box) >= NOVELTY_IOU for box in detector_boxes):
            continue
        if any(
            item.label == anomaly.label
            and _iou(anomaly.bbox_xyxy, item.bbox_xyxy) >= 0.5
            for item in accepted
        ):
            continue
        accepted.append(
            LabeledBox(
                label=anomaly.label,
                bbox_xyxy=tuple(float(value) for value in anomaly.bbox_xyxy),
                score=1.0,
            )
        )
    return accepted
