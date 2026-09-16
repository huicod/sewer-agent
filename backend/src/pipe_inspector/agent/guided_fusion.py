"""Shared deterministic fusion for guided detector review."""

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

from pipe_inspector.agent.guided_review import GUIDED_HIGH_SCORE_THRESHOLD, GuidedReview
from pipe_inspector.contracts.detection import DetectionLabel
from pipe_inspector.contracts.inspection import ImageObservation

FUSION_VERSION = "guided-confidence-v2"
NOVELTY_IOU = 0.3
ADDITION_DUPLICATE_IOU = 0.5
LABELS = tuple(label.value for label in DetectionLabel)


class _LabeledBox(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: DetectionLabel
    bbox_xyxy: tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat]
    score: float = Field(default=1.0, ge=0.0, le=1.0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_coordinates(self) -> "_LabeledBox":
        x1, y1, x2, y2 = self.bbox_xyxy
        if x2 <= x1 or y2 <= y1:
            raise ValueError("box must have positive area")
        return self


def _iou(left: Sequence[float], right: Sequence[float]) -> float:
    lx1, ly1, lx2, ly2 = left
    rx1, ry1, rx2, ry2 = right
    width = max(0.0, min(lx2, rx2) - max(lx1, rx1))
    height = max(0.0, min(ly2, ry2) - max(ly1, ry1))
    intersection = width * height
    if intersection == 0:
        return 0.0
    left_area = (lx2 - lx1) * (ly2 - ly1)
    right_area = (rx2 - rx1) * (ry2 - ry1)
    return intersection / (left_area + right_area - intersection)


def _raw_boxes(observation: ImageObservation) -> list[_LabeledBox]:
    return [
        _LabeledBox(label=item.label, bbox_xyxy=item.bbox_xyxy, score=item.score)
        for item in observation.detection.detections
    ]


def fuse_guided(observation: ImageObservation, review: GuidedReview | None) -> dict:
    """Apply guided-confidence-v2 without changing its accepted output."""
    raw = _raw_boxes(observation)
    predictions, changes = [], []
    by_index = {c.index: c for c in review.candidates} if review else {}
    for index, original in enumerate(raw):
        advice = by_index.get(index)
        clear = bool(
            review and review.image_assessable and advice and advice.evidence_strength == "clear"
        )
        action, pending, prediction = "retain_pending", True, original
        gate = None
        if clear and advice.verdict == "accept":
            action, pending = "accept", False
        elif (
            clear
            and advice.verdict in ("reject", "revise")
            and original.score >= GUIDED_HIGH_SCORE_THRESHOLD
        ):
            action = "retain_high_confidence"
            gate = "高置信度原类别、原框和原分数保留；VLM 否决/改类建议仅交人工复审"
        elif clear and advice.verdict == "reject":
            action, pending, prediction = "reject", False, None
        elif clear and advice.verdict == "revise":
            action, pending = "revise", False
            prediction = original.model_copy(update={"label": advice.proposed_label})
        if prediction is not None:
            predictions.append(prediction)
        changes.append(
            dict(
                index=index,
                action=action,
                pending=pending,
                label=prediction.label.value if prediction else original.label.value,
                bbox_xyxy=list(original.bbox_xyxy),
                reason=advice.reason if advice else "复核失败或缺失，保留原检测待审",
                vlm_verdict=advice.verdict if advice else None,
                proposed_label=advice.proposed_label if advice else None,
                gate=gate,
            )
        )
    accepted_additions = []
    for index, anomaly in enumerate(review.additional_anomalies if review else ()):
        box = anomaly.bbox_xyxy
        action = "add_pending"
        if anomaly.label not in LABELS:
            gate = "其他类别交人工确认，不纳入五类预测"
        elif not review.image_assessable or anomaly.evidence_strength != "clear" or box is None:
            gate = "缺少明确且可定位的证据"
        elif any(_iou(box, p.bbox_xyxy) >= NOVELTY_IOU for p in raw):
            gate = "与原候选重叠，不能绕过逐框裁决重复新增"
        elif any(
            other.label in LABELS
            and other.label != anomaly.label
            and other.evidence_strength == "clear"
            and other.bbox_xyxy is not None
            and _iou(box, other.bbox_xyxy) >= ADDITION_DUPLICATE_IOU
            for other in review.additional_anomalies
        ):
            gate = "同一区域新增类别相互冲突，两者均待审，不凭输出顺序选一类"
        elif any(
            p.label == anomaly.label and _iou(box, p.bbox_xyxy) >= ADDITION_DUPLICATE_IOU
            for p in accepted_additions
        ):
            gate = "与已新增同类框重复"
        else:
            action, gate = "add", "明确、可定位且未与原候选重复"
            prediction = _LabeledBox(label=anomaly.label, bbox_xyxy=box, score=1.0)
            accepted_additions.append(prediction)
            predictions.append(prediction)
        changes.append(
            dict(
                additional_index=index,
                action=action,
                pending=action != "add",
                label=str(anomaly.label),
                bbox_xyxy=list(box) if box else None,
                reason=anomaly.reason,
                gate=gate,
            )
        )
    return dict(predictions=[p.model_dump(mode="json") for p in predictions], changes=changes)
