"""Pure metrics and neutral human-review export for incremental evaluation."""

from __future__ import annotations

import csv
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from uuid import UUID

from pipe_inspector.contracts.open_scan import OpenCandidate
from pipe_inspector.evaluation.review import LABELS, LabeledBox, _matched_pairs, score_boxes

STAGES = ("primary", "guided", "combined")
SOURCE_KINDS = ("image", "video")
OPEN_STATUSES = ("skipped", "succeeded", "failed")
REVIEW_COLUMNS = (
    "sample_id",
    "finding_id",
    "group_id",
    "image_path",
    "frame_index",
    "timestamp_seconds",
    "gallery_link",
    "ordinal",
    "source_kind",
    "label",
    "name",
    "score",
    "bbox_x1",
    "bbox_y1",
    "bbox_x2",
    "bbox_y2",
    "decision",
    "corrected_label",
    "comment",
)


def _rates(tp: int, fp: int, fn: int) -> dict[str, int | float]:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1}


def _merge_annotation_metrics(
    items: Sequence[tuple[Sequence[LabeledBox], Sequence[LabeledBox]]],
) -> dict[str, Any] | None:
    if not items:
        return None
    totals = {label: {"tp": 0, "fp": 0, "fn": 0} for label in LABELS}
    for ground_truth, predictions in items:
        result = score_boxes(ground_truth, predictions)
        for label in LABELS:
            for key in ("tp", "fp", "fn"):
                totals[label][key] += int(result["per_class"][label][key])
    per_class = {label: _rates(**totals[label]) for label in LABELS}
    micro = {key: sum(totals[label][key] for label in LABELS) for key in ("tp", "fp", "fn")}
    return {"micro": _rates(**micro), "per_class": per_class}


def _boxes(value: Any, *, field: str, sample_id: str) -> list[LabeledBox]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list for {sample_id}")
    try:
        return [LabeledBox.model_validate(item) for item in value]
    except (TypeError, ValueError) as error:
        raise ValueError(f"invalid {field} for {sample_id}") from error


def _open_candidates(value: Any, *, sample_id: str) -> list[OpenCandidate]:
    if not isinstance(value, list):
        raise ValueError(f"open_added must be a list for {sample_id}")
    try:
        return [OpenCandidate.model_validate(item) for item in value]
    except (TypeError, ValueError) as error:
        raise ValueError(f"invalid open_added for {sample_id}") from error


def _optional_total(records: Sequence[Mapping[str, Any]], key: str) -> int | None:
    if not all(key in record for record in records):
        return None
    return sum(int(record[key]) for record in records)


def _stage_report(
    metrics: dict[str, Any] | None,
    *,
    stage: str,
) -> dict[str, Any]:
    return {
        "annotation_metrics": metrics,
        "annotation_unmatched_prediction_count": (
            int(metrics["micro"]["fp"]) if metrics is not None else None
        ),
        "annotation_unmatched_predictions_are_proven_false_positives": False,
        "accuracy_claim": (
            "tentative_candidate_coverage_only"
            if stage == "combined"
            else "matched_original_annotations_only"
        ),
    }


def summarize_incremental(records: list[dict], *, expected_count: int) -> dict:
    """Summarize fixed records without treating unknown truth as an empty annotation."""
    if expected_count < len(records) or expected_count < 0:
        raise ValueError("expected_count must be nonnegative and at least the record count")

    sample_ids = [record.get("sample_id") for record in records]
    if any(not isinstance(sample_id, str) or not sample_id for sample_id in sample_ids):
        raise ValueError("sample_id must be a nonempty string")
    if len(sample_ids) != len(set(sample_ids)):
        raise ValueError("sample_id values must be unique")

    cohorts: dict[str, dict[str, Any]] = {}
    normalized: list[dict[str, Any]] = []
    for record in records:
        sample_id = record["sample_id"]
        source_kind = record.get("source_kind")
        if source_kind not in SOURCE_KINDS:
            raise ValueError(f"invalid source_kind for {sample_id}")
        raw_ground_truth = record.get("ground_truth")
        ground_truth = (
            None
            if raw_ground_truth is None
            else _boxes(raw_ground_truth, field="ground_truth", sample_id=sample_id)
        )
        stages = {
            stage: _boxes(record.get(stage), field=stage, sample_id=sample_id) for stage in STAGES
        }
        open_added = _open_candidates(record.get("open_added"), sample_id=sample_id)
        open_status = record.get("open_status")
        if open_status not in OPEN_STATUSES:
            raise ValueError(f"invalid open_status for {sample_id}")
        duration = float(record.get("open_duration_seconds"))
        if not math.isfinite(duration) or duration < 0:
            raise ValueError(f"invalid open_duration_seconds for {sample_id}")
        normalized.append(
            {
                "record": record,
                "sample_id": sample_id,
                "source_kind": source_kind,
                "ground_truth": ground_truth,
                "stages": stages,
                "open_added": open_added,
                "open_status": open_status,
                "open_duration_seconds": duration,
            }
        )

    for source_kind in SOURCE_KINDS:
        members = [item for item in normalized if item["source_kind"] == source_kind]
        annotated = [item for item in members if item["ground_truth"] is not None]
        stage_metrics = {
            stage: _merge_annotation_metrics(
                [(item["ground_truth"], item["stages"][stage]) for item in annotated]
            )
            for stage in STAGES
        }
        per_image_deltas = []
        for item in annotated:
            target_sets = {
                stage: {
                    target_index
                    for _, target_index in _matched_pairs(
                        item["ground_truth"], item["stages"][stage]
                    )
                }
                for stage in STAGES
            }
            per_image_deltas.append(
                {
                    "sample_id": item["sample_id"],
                    "primary_to_guided": {
                        "recovered": len(target_sets["guided"] - target_sets["primary"]),
                        "lost": len(target_sets["primary"] - target_sets["guided"]),
                    },
                    "guided_to_combined": {
                        "recovered": len(target_sets["combined"] - target_sets["guided"]),
                        "lost": len(target_sets["guided"] - target_sets["combined"]),
                    },
                }
            )
        cohorts[source_kind] = {
            "record_count": len(members),
            "annotated_count": len(annotated),
            "unknown_ground_truth_count": len(members) - len(annotated),
            "stages": {stage: _stage_report(stage_metrics[stage], stage=stage) for stage in STAGES},
            "per_image_deltas": per_image_deltas,
        }

    status_counts = {status: 0 for status in OPEN_STATUSES}
    for item in normalized:
        status_counts[item["open_status"]] += 1
    pending = [candidate for item in normalized for candidate in item["open_added"]]
    source_records = [item["record"] for item in normalized]
    missing_count = expected_count - len(records)
    return {
        "status": "complete" if missing_count == 0 else "incomplete",
        "complete": missing_count == 0,
        "coverage": {
            "expected_count": expected_count,
            "record_count": len(records),
            "missing_count": missing_count,
        },
        "cohorts": cohorts,
        "operations": {
            "open_detection": {
                "status_counts": status_counts,
                "pending_count": len(pending),
                "pending_other_count": sum(candidate.label == "OTHER" for candidate in pending),
                "duration_seconds": sum(item["open_duration_seconds"] for item in normalized),
            },
            "review": {
                "cached_count": sum(bool(record.get("review_cached")) for record in records),
                "new_vlm_calls": 0,
                "failure_count": sum(record.get("review_error") is not None for record in records),
                "abstention_count": _optional_total(source_records, "review_abstained"),
                "pending_change_count": _optional_total(source_records, "pending_change_count"),
            },
            "cost": {"currency": None, "amount": None},
        },
    }


def export_review_queue(
    records: list[dict], path: Path, *, samples: list[dict] | None = None
) -> Path:
    """Write open candidates once; preserve an existing CSV and any human edits."""
    path = Path(path)
    if path.exists():
        return path

    sample_ids = [record.get("sample_id") for record in records]
    if len(sample_ids) != len(set(sample_ids)):
        raise ValueError("sample_id values must be unique")

    rows: list[dict[str, Any]] = []
    source_map = {
        sample["sample_id"]: (index, sample) for index, sample in enumerate(samples or records, 1)
    }
    for record in records:
        sample_id = record.get("sample_id")
        source_kind = record.get("source_kind")
        if not isinstance(sample_id, str) or not sample_id:
            raise ValueError("sample_id must be a nonempty string")
        if source_kind not in SOURCE_KINDS:
            raise ValueError(f"invalid source_kind for {sample_id}")
        if sample_id not in source_map:
            raise ValueError("review queue source alignment differs")
        position, source = source_map[sample_id]
        candidates = _open_candidates(record.get("open_added"), sample_id=sample_id)
        findings = record.get("open_findings", [])
        if len(findings) != len(candidates):
            raise ValueError("open candidate/finding alignment differs")
        for ordinal, (candidate, finding) in enumerate(zip(candidates, findings, strict=True), 1):
            if (
                finding["canonical_label"] != candidate.label
                or tuple(finding["evidence"][0]["region"]["bbox_xyxy"]) != candidate.bbox_xyxy
            ):
                raise ValueError("open candidate/finding alignment differs")
            x1, y1, x2, y2 = candidate.bbox_xyxy
            rows.append(
                {
                    "sample_id": sample_id,
                    "finding_id": str(UUID(finding["finding_id"])),
                    "group_id": source.get("group_id", ""),
                    "image_path": source.get("image_path", ""),
                    "frame_index": source.get("frame_index"),
                    "timestamp_seconds": source.get("timestamp_seconds"),
                    "gallery_link": f"gallery/sample-{position:04d}/index.html",
                    "ordinal": ordinal,
                    "source_kind": source_kind,
                    "label": candidate.label.value
                    if hasattr(candidate.label, "value")
                    else candidate.label,
                    "name": candidate.name,
                    "score": candidate.score,
                    "bbox_x1": x1,
                    "bbox_y1": y1,
                    "bbox_x2": x2,
                    "bbox_y2": y2,
                    "decision": "unreviewed",
                    "corrected_label": "",
                    "comment": "",
                }
            )

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return path
