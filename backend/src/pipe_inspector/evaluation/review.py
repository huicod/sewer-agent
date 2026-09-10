"""Deterministic paired scoring for the fixed detector/VLM review pilot."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, field_validator, model_validator

from pipe_inspector.agent.api_client import APIClientError
from pipe_inspector.agent.image_policy import STRONG_EVIDENCE_SCORE, decide_image
from pipe_inspector.agent.image_review import review_required
from pipe_inspector.contracts.assets import AssetRef
from pipe_inspector.contracts.detection import DetectionBatch, DetectionLabel
from pipe_inspector.contracts.findings import ReviewStatus
from pipe_inspector.contracts.inspection import ImageObservation, ImageReview, QualitySummary

MATCH_IOU = 0.5
DETECTOR_CONF = 0.25
DETECTOR_IMGSZ = 960
LABELS = tuple(label.value for label in DetectionLabel)


class LabeledBox(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: DetectionLabel
    bbox_xyxy: tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat]
    score: float = Field(default=1.0, ge=0.0, le=1.0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_coordinates(self) -> "LabeledBox":
        x1, y1, x2, y2 = self.bbox_xyxy
        if x2 <= x1 or y2 <= y1:
            raise ValueError("box must have positive area")
        return self


class EvaluationSample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_id: str = Field(min_length=1)
    group_id: str = Field(min_length=1)
    image_path: Path
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    ground_truth: tuple[LabeledBox, ...] = ()

    @field_validator("sample_id", "group_id")
    @classmethod
    def validate_identity(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("sample and group identity must be nonempty")
        return value

    @field_validator("image_path")
    @classmethod
    def validate_image_path(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("image_path must be an absolute local path")
        return value

    @model_validator(mode="after")
    def validate_ground_truth_bounds(self) -> "EvaluationSample":
        for item in self.ground_truth:
            x1, y1, x2, y2 = item.bbox_xyxy
            if x1 < 0 or y1 < 0 or x2 > self.width or y2 > self.height:
                raise ValueError("ground-truth box falls outside original image")
        return self


class EvaluationManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_name: str = Field(min_length=1)
    split_provenance: str = Field(min_length=1)
    samples: tuple[EvaluationSample, ...] = Field(min_length=1)

    @field_validator("dataset_name", "split_provenance")
    @classmethod
    def validate_factual_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("dataset name and split provenance must be nonempty")
        return value

    @model_validator(mode="after")
    def validate_unique_pilot_units(self) -> "EvaluationManifest":
        sample_ids = [sample.sample_id for sample in self.samples]
        group_ids = [sample.group_id for sample in self.samples]
        if len(sample_ids) != len(set(sample_ids)):
            raise ValueError("sample_id values must be unique")
        if len(group_ids) != len(set(group_ids)):
            raise ValueError("pilot requires one sample per group_id")
        return self

    def enforce_limit(self, max_samples: int) -> None:
        if max_samples <= 0 or len(self.samples) > max_samples:
            raise ValueError(
                f"manifest has {len(self.samples)} samples; max_samples is {max_samples}"
            )


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


def _rates(tp: int, fp: int, fn: int) -> dict[str, int | float]:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1}


def score_boxes(
    ground_truth: Sequence[LabeledBox],
    predictions: Sequence[LabeledBox],
    *,
    iou_threshold: float = MATCH_IOU,
) -> dict[str, Any]:
    """Match predictions greedily by descending score, class, and IoU."""
    counts = {label: {"tp": 0, "fp": 0, "fn": 0} for label in LABELS}
    matched: set[int] = set()
    ordered = sorted(enumerate(predictions), key=lambda item: (-item[1].score, item[0]))
    for _, prediction in ordered:
        best_index = None
        best_iou = -1.0
        for index, target in enumerate(ground_truth):
            if index in matched or target.label != prediction.label:
                continue
            overlap = _iou(target.bbox_xyxy, prediction.bbox_xyxy)
            if overlap > best_iou:
                best_index, best_iou = index, overlap
        label_counts = counts[prediction.label.value]
        if best_index is not None and best_iou >= iou_threshold:
            matched.add(best_index)
            label_counts["tp"] += 1
        else:
            label_counts["fp"] += 1
    for index, target in enumerate(ground_truth):
        if index not in matched:
            counts[target.label.value]["fn"] += 1

    per_class = {label: _rates(**counts[label]) for label in LABELS}
    totals = {
        key: sum(int(per_class[label][key]) for label in LABELS) for key in ("tp", "fp", "fn")
    }
    return {"micro": _rates(**totals), "per_class": per_class}


def _raw_boxes(batch: DetectionBatch) -> list[LabeledBox]:
    return [
        LabeledBox(label=item.label, bbox_xyxy=item.bbox_xyxy, score=item.score)
        for item in batch.detections
    ]


def _finding_boxes(decision: Mapping[str, Any], *, confirmed_only: bool) -> list[LabeledBox]:
    boxes: list[LabeledBox] = []
    for finding in decision["findings"]:
        status = finding["review_status"]
        if status == ReviewStatus.REJECTED.value:
            continue
        if confirmed_only and status != ReviewStatus.CONFIRMED.value:
            continue
        label = finding.get("canonical_label")
        if label not in LABELS:
            continue
        region = finding["evidence"][0].get("region")
        if region is None:
            continue
        boxes.append(
            LabeledBox(
                label=label,
                bbox_xyxy=region["bbox_xyxy"],
                score=finding.get("score") or 0.0,
            )
        )
    return boxes


def _safe_error(error: Exception) -> str:
    return error.code if isinstance(error, APIClientError) else "unexpected_error"


def _write_state(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


async def evaluate_manifest(
    manifest: EvaluationManifest,
    output_dir: Path,
    *,
    pins: Mapping[str, Any],
    detector: Any,
    reviewer: Any,
    quality_analyzer: Callable[[Path], QualitySummary],
    max_samples: int = 48,
) -> list[dict[str, Any]]:
    """Run uncached samples sequentially and checkpoint safe records after each sample."""
    manifest.enforce_limit(max_samples)
    state_path = output_dir / "state.json"
    manifest_json = manifest.model_dump(mode="json")
    pins_json = json.loads(json.dumps(dict(pins), sort_keys=True))
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("manifest") != manifest_json:
            raise ValueError("saved manifest does not match requested manifest")
        if state.get("pins") != pins_json:
            raise ValueError("saved pins do not match requested pins")
    else:
        state = {"manifest": manifest_json, "pins": pins_json, "records": []}
        _write_state(state_path, state)

    cached_ids = {record["sample_id"] for record in state["records"]}
    for sample in manifest.samples:
        if sample.sample_id in cached_ids:
            continue
        if not sample.image_path.is_file():
            raise FileNotFoundError(f"evaluation image does not exist: {sample.image_path}")
        detection = detector.predict_image(sample.image_path)
        if (detection.width, detection.height) != (sample.width, sample.height):
            raise ValueError(f"detector dimensions differ from manifest for {sample.sample_id}")
        observation = ImageObservation(
            detection=detection,
            quality=quality_analyzer(sample.image_path),
        )
        review: ImageReview | None = None
        review_error: str | None = None
        if review_required(observation):
            try:
                review = await reviewer.review(sample.image_path, observation)
            except Exception as error:  # provider details must never enter persisted output
                review_error = _safe_error(error)
        task_id = uuid5(NAMESPACE_URL, f"evaluation-task:{sample.sample_id}")
        asset = AssetRef(asset_id=uuid5(NAMESPACE_URL, f"evaluation-asset:{sample.sample_id}"))
        decision = decide_image(task_id, asset, observation, review, review_error=review_error)
        state["records"].append(
            {
                "sample_id": sample.sample_id,
                "observation": observation.model_dump(mode="json"),
                "review": review.model_dump(mode="json") if review is not None else None,
                "review_error": review_error,
                "decision": decision.model_dump(mode="json"),
            }
        )
        _write_state(state_path, state)
    return list(state["records"])


def _merge_scores(
    items: Sequence[tuple[Sequence[LabeledBox], Sequence[LabeledBox]]],
) -> dict[str, Any]:
    totals = {label: {"tp": 0, "fp": 0, "fn": 0} for label in LABELS}
    for targets, outputs in items:
        sample_score = score_boxes(targets, outputs)
        for label in LABELS:
            for key in ("tp", "fp", "fn"):
                totals[label][key] += int(sample_score["per_class"][label][key])
    per_class = {label: _rates(**totals[label]) for label in LABELS}
    micro_counts = {key: sum(totals[label][key] for label in LABELS) for key in ("tp", "fp", "fn")}
    result = {"micro": _rates(**micro_counts), "per_class": per_class}
    result["fp_per_image"] = result["micro"]["fp"] / len(items) if items else 0.0
    return result


def summarize(
    manifest: EvaluationManifest,
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Recompute all metrics from saved records without detector or provider access."""
    by_id = {record["sample_id"]: record for record in records}
    expected_ids = [sample.sample_id for sample in manifest.samples]
    evaluated_ids = [sample_id for sample_id in expected_ids if sample_id in by_id]
    missing_ids = [sample_id for sample_id in expected_ids if sample_id not in by_id]
    coverage = {
        "status": "complete" if not missing_ids else "incomplete",
        "complete": not missing_ids,
        "expected_sample_count": len(expected_ids),
        "expected_sample_ids": expected_ids,
        "evaluated_sample_count": len(evaluated_ids),
        "evaluated_sample_ids": evaluated_ids,
        "missing_sample_count": len(missing_ids),
        "missing_sample_ids": missing_ids,
    }
    if missing_ids:
        return coverage

    raw_items = []
    queue_items = []
    confirmed_items = []
    improved = worsened = unchanged = 0
    pending = abstain = failures = nonlocalized = 0
    raw_exact = queue_exact = confirmed_exact = suggested_exact = 0

    for sample in manifest.samples:
        record = by_id.get(sample.sample_id)
        if record is None:
            continue
        targets = list(sample.ground_truth)
        batch = DetectionBatch.model_validate(record["observation"]["detection"])
        raw = _raw_boxes(batch)
        queue = _finding_boxes(record["decision"], confirmed_only=False)
        confirmed = _finding_boxes(record["decision"], confirmed_only=True)
        raw_items.append((targets, raw))
        queue_items.append((targets, queue))
        confirmed_items.append((targets, confirmed))

        target_labels = {item.label.value for item in targets}
        raw_labels = {item.label.value for item in raw}
        queue_labels = {item.label.value for item in queue}
        confirmed_labels = {item.label.value for item in confirmed}
        raw_ok, queue_ok = raw_labels == target_labels, queue_labels == target_labels
        raw_exact += int(raw_ok)
        queue_exact += int(queue_ok)
        confirmed_exact += int(confirmed_labels == target_labels)
        if queue_ok and not raw_ok:
            improved += 1
        elif raw_ok and not queue_ok:
            worsened += 1
        else:
            unchanged += 1

        review = record.get("review")
        suggested_labels = set(queue_labels)
        if review is not None:
            abstain += sum(
                candidate["verdict"] in {"abstain", "need_more_context"}
                for candidate in review["candidates"]
            )
            for anomaly in review["additional_anomalies"]:
                if anomaly["label"] in LABELS:
                    suggested_labels.add(anomaly["label"])
                if anomaly.get("bbox_xyxy") is None:
                    nonlocalized += 1
        suggested_exact += int(suggested_labels == target_labels)
        pending += sum(
            finding["review_status"]
            in {ReviewStatus.NEEDS_REVIEW.value, ReviewStatus.TENTATIVE.value}
            for finding in record["decision"]["findings"]
        )
        failures += int(record.get("review_error") is not None)

    evaluated = len(raw_items)

    def exact(count: int) -> float:
        return count / evaluated if evaluated else 0.0

    raw_score = _merge_scores(raw_items)
    queue_score = _merge_scores(queue_items)
    confirmed_score = _merge_scores(confirmed_items)

    def delta(candidate: Mapping[str, Any]) -> dict[str, float]:
        return {
            "micro_precision": candidate["micro"]["precision"] - raw_score["micro"]["precision"],
            "micro_recall": candidate["micro"]["recall"] - raw_score["micro"]["recall"],
            "micro_f1": candidate["micro"]["f1"] - raw_score["micro"]["f1"],
            "fp_per_image": candidate["fp_per_image"] - raw_score["fp_per_image"],
        }

    return {
        **coverage,
        "sample_count": evaluated,
        "operating_point": {"conf": DETECTOR_CONF, "imgsz": DETECTOR_IMGSZ, "iou": MATCH_IOU},
        "raw_detector": raw_score,
        "retained_queue": queue_score,
        "auto_confirmed": confirmed_score,
        "paired_deltas": {
            "retained_queue_minus_raw": delta(queue_score),
            "auto_confirmed_minus_raw": delta(confirmed_score),
        },
        "image_label_set_exact": {
            "raw_detector": exact(raw_exact),
            "retained_queue": exact(queue_exact),
            "auto_confirmed": exact(confirmed_exact),
            "vlm_suggestion_inclusive": exact(suggested_exact),
        },
        "paired_image_label_sets": {
            "improved": improved,
            "worsened": worsened,
            "unchanged": unchanged,
        },
        "pending_findings": pending,
        "abstain_or_more_context": abstain,
        "review_failures": failures,
        "nonlocalized_vlm_suggestions": nonlocalized,
        "claims": {"true_normal_accuracy": None, "map": None},
    }


def build_live_components(settings: Any) -> tuple[Any, Any, dict[str, Any]]:
    """Validate nonsecret live configuration before constructing the detector."""
    if not settings.vlm_configured:
        raise ValueError("complete VLM configuration is required for --live")
    parsed = urlsplit(settings.vlm_base_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("VLM configuration contains an unsafe base URL")

    from pipe_inspector.agent.api_client import OpenAICompatibleClient
    from pipe_inspector.agent.image_review import (
        IMAGE_REVIEW_PROMPT_VERSION,
        REVIEW_ALWAYS_LABELS,
        REVIEW_SCORE_THRESHOLD,
        ImageReviewer,
    )
    from pipe_inspector.vision.detector import (
        DETECTOR_ID,
        LABEL_SPACE_VERSION,
        UltralyticsDetector,
    )
    from pipe_inspector.vision.quality import QUALITY_POLICY_VERSION

    client = OpenAICompatibleClient(
        settings.vlm_base_url,
        settings.vlm_model,
        settings.vlm_api_key,
        timeout_seconds=settings.api_timeout_seconds,
        enable_thinking=settings.api_enable_thinking,
        max_output_tokens=settings.api_max_output_tokens,
    )
    reviewer = ImageReviewer(client, max_image_bytes=settings.vlm_max_image_bytes)
    detector = UltralyticsDetector(
        settings.detector_path,
        device=settings.detector_device,
        imgsz=DETECTOR_IMGSZ,
        conf=DETECTOR_CONF,
    )
    pins = {
        "detector": {
            "detector_id": DETECTOR_ID,
            "label_space_version": LABEL_SPACE_VERSION,
            "checkpoint": str(settings.detector_path),
            "model_version": detector.model_version,
            "device": settings.detector_device,
            "imgsz": DETECTOR_IMGSZ,
            "conf": DETECTOR_CONF,
            "nms_iou": 0.7,
        },
        "vlm": {
            "base_url": settings.vlm_base_url,
            "model": settings.vlm_model,
            "prompt_version": IMAGE_REVIEW_PROMPT_VERSION,
            "review_score_threshold": REVIEW_SCORE_THRESHOLD,
            "review_always_labels": sorted(label.value for label in REVIEW_ALWAYS_LABELS),
            "api_enable_thinking": settings.api_enable_thinking,
            "api_max_output_tokens": settings.api_max_output_tokens,
            "api_timeout_seconds": settings.api_timeout_seconds,
            "vlm_max_image_bytes": settings.vlm_max_image_bytes,
        },
        "quality_policy_version": QUALITY_POLICY_VERSION,
        "image_decision_policy": {"strong_evidence_score": STRONG_EVIDENCE_SCORE},
    }
    return detector, reviewer, pins


def cli_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score a fixed paired detector/VLM review pilot.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-samples", type=int, default=48)
    parser.add_argument(
        "--live",
        action="store_true",
        help=(
            "Explicitly enable detector and at most one VLM review call per uncached "
            "eligible image."
        ),
    )
    args = parser.parse_args(argv)
    manifest = EvaluationManifest.model_validate_json(args.manifest.read_text(encoding="utf-8"))
    manifest.enforce_limit(args.max_samples)

    if args.live:
        from pipe_inspector.config import Settings
        from pipe_inspector.vision.quality import analyze_quality

        detector, reviewer, pins = build_live_components(Settings())
        records = asyncio.run(
            evaluate_manifest(
                manifest,
                args.output,
                pins=pins,
                detector=detector,
                reviewer=reviewer,
                quality_analyzer=analyze_quality,
                max_samples=args.max_samples,
            )
        )
    else:
        state_path = args.output / "state.json"
        if not state_path.is_file():
            raise FileNotFoundError("saved evaluation state does not exist; use --live to run")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("manifest") != manifest.model_dump(mode="json"):
            raise ValueError("saved manifest does not match requested manifest")
        records = state.get("records", [])

    summary = summarize(manifest, records)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if summary["complete"] else 2
