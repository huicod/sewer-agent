"""Offline multilabel scoring of saved calibration predictions, with box references.

No detector, reviewer or provider is constructed. The calibration cohort and
its saved predictions are immutable inputs, including failed review attempts.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pipe_inspector.evaluation.review import LABELS, MATCH_IOU, LabeledBox, _rates, score_boxes

SOURCE_LABELS = {"sewer2403": LABELS, "sedd": ("PL", "BLOCK")}


def _boxes(items: Any, *, detector: bool = False) -> list[LabeledBox]:
    if not isinstance(items, list):
        raise ValueError("box collections must be lists")
    if detector:
        items = [{key: item[key] for key in ("label", "bbox_xyxy", "score")} for item in items]
    return [LabeledBox.model_validate(item) for item in items]


def _cohort(state: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if state.get("schema_version") != "vlm-calibration-live-v1":
        raise ValueError("expected vlm-calibration-live-v1 state")
    records, ids = state.get("records"), state.get("sample_ids")
    if not isinstance(records, list) or not records or not isinstance(ids, list):
        raise ValueError("state requires nonempty records and sample_ids")
    if any(not isinstance(value, str) or not value for value in ids) or len(set(ids)) != len(ids):
        raise ValueError("sample_ids must be unique nonempty strings")
    by_id = {}
    for record in records:
        if not isinstance(record, Mapping):
            raise ValueError("records must be objects")
        sample_id = record.get("sample_id")
        if not isinstance(sample_id, str) or sample_id not in ids or sample_id in by_id:
            raise ValueError("records must contain exactly one entry per sample_id")
        if record.get("dataset") not in SOURCE_LABELS:
            raise ValueError(f"unknown annotation scope for {sample_id}")
        by_id[sample_id] = record
    if set(by_id) != set(ids):
        raise ValueError("missing cohort records; refusing to change the denominator")
    return [by_id[sample_id] for sample_id in ids]


def _confirmations(
    confirmation: Mapping[str, Any] | None,
    records: Sequence[Mapping[str, Any]],
) -> tuple[set[str], dict[str, Any]]:
    proposals = {
        f"{record['sample_id']}::{index}": (record, proposal)
        for record in records
        for index, proposal in enumerate(_boxes(record["experimental_additions"]))
    }
    stats = dict(
        provided=confirmation is not None,
        proposals=len(proposals),
        reviewed=0,
        accepted=0,
        failed=0,
        missing=len(proposals),
    )
    if confirmation is None:
        return set(), stats
    if confirmation.get("schema_version") != "vlm-calibration-addition-confirmation-v1":
        raise ValueError("expected vlm-calibration-addition-confirmation-v1 state")
    items = confirmation.get("records")
    if not isinstance(items, list):
        raise ValueError("confirmation records must be a list")
    seen, accepted = set(), set()
    for item in items:
        if not isinstance(item, Mapping):
            raise ValueError("confirmation records must be objects")
        key = item.get("key")
        if not isinstance(key, str) or key not in proposals or key in seen:
            raise ValueError("duplicate or unknown confirmation proposal key")
        record, proposal = proposals[key]
        if (
            item.get("sample_id") != record["sample_id"]
            or item.get("dataset") != record["dataset"]
            or LabeledBox.model_validate(item.get("proposal")) != proposal
        ):
            raise ValueError("confirmation identity/proposal differs from the source")
        if not isinstance(item.get("accepted"), bool):
            raise ValueError("confirmation accepted must be a boolean")
        seen.add(key)
        if item.get("review_error"):
            stats["failed"] += 1
        elif item["accepted"]:
            accepted.add(key)
    stats.update(reviewed=len(seen), accepted=len(accepted), missing=len(proposals) - len(seen))
    return accepted, stats


def _label_score(
    ground_truth: list[LabeledBox],
    predictions: list[LabeledBox],
    allowed: Sequence[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    targets = {box.label.value for box in ground_truth}
    predicted = {box.label.value for box in predictions}
    gt, pred = targets.intersection(allowed), predicted.intersection(allowed)
    tp, fp, fn = gt & pred, pred - gt, gt - pred
    detail = {
        "predicted_labels": sorted(pred),
        "ignored_prediction_labels": sorted(predicted - set(allowed)),
        "tp_labels": sorted(tp),
        "fp_labels": sorted(fp),
        "fn_labels": sorted(fn),
        "exact_match": gt == pred,
    }
    score = {
        "micro": _rates(len(tp), len(fp), len(fn)),
        "per_class": {
            label: _rates(int(label in tp), int(label in fp), int(label in fn)) for label in allowed
        },
    }
    return detail, score


def _aggregate(rows: Sequence[Mapping[str, Any]], variant: str, level: str) -> dict[str, Any]:
    result = {}
    for source in ("mixed", *SOURCE_LABELS):
        subset = list(rows) if source == "mixed" else [r for r in rows if r["dataset"] == source]
        labels = LABELS if source == "mixed" else SOURCE_LABELS[source]
        per_class = {}
        for label in labels:
            applicable = [r for r in subset if label in SOURCE_LABELS[r["dataset"]]]
            counts = {
                key: sum(r["scores"][variant][level]["per_class"][label][key] for r in applicable)
                for key in ("tp", "fp", "fn")
            }
            per_class[label] = _rates(**counts) | {"evaluated_images": len(applicable)}
        totals = {key: sum(c[key] for c in per_class.values()) for key in ("tp", "fp", "fn")}
        summary = {"images": len(subset), "micro": _rates(**totals), "per_class": per_class}
        if level == "image_level":
            exact = sum(r["variants"][variant]["exact_match"] for r in subset)
            summary.update(
                exact_match_count=exact, exact_match_rate=exact / len(subset) if subset else None
            )
        result[source] = summary
    return result


def evaluate_cached(
    state: Mapping[str, Any],
    confirmation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Score all saved images; partial second reviews are reported, never accepted."""
    records = _cohort(state)
    try:
        accepted, confirmation_stats = _confirmations(confirmation, records)
        variants = ["raw_yolo", "conservative", "experimental"]
        if confirmation is not None:
            variants.append("confirmed_additions")
        rows = []
        for record in records:
            allowed = SOURCE_LABELS[record["dataset"]]
            gt = _boxes(record["ground_truth"])
            scoped_gt = [box for box in gt if box.label.value in allowed]
            predictions = {
                "raw_yolo": _boxes(record["observation"]["detection"]["detections"], detector=True),
                "conservative": _boxes(record["conservative_predictions"]),
                "experimental": _boxes(record["predictions"]),
            }
            if confirmation is not None:
                predictions["confirmed_additions"] = predictions["conservative"] + [
                    proposal
                    for index, proposal in enumerate(_boxes(record["experimental_additions"]))
                    if f"{record['sample_id']}::{index}" in accepted
                ]
            row = {
                "sample_id": record["sample_id"],
                "dataset": record["dataset"],
                "ground_truth_labels": sorted({box.label.value for box in scoped_gt}),
                "ignored_ground_truth_labels": sorted(
                    {box.label.value for box in gt if box.label.value not in allowed}
                ),
                "review_failed": bool(record.get("review_error")),
                "variants": {},
                "scores": {},
            }
            for variant, predicted in predictions.items():
                detail, scores = _label_score(gt, predicted, allowed)
                row["variants"][variant] = detail
                row["scores"][variant] = {
                    "image_level": scores,
                    "box_level": score_boxes(
                        scoped_gt,
                        [box for box in predicted if box.label.value in allowed],
                        iou_threshold=MATCH_IOU,
                    ),
                }
            rows.append(row)
    except (KeyError, TypeError) as exc:
        raise ValueError("incomplete calibration record; no samples were scored") from exc
    return {
        "schema_version": "image-level-calibration-v1",
        "protocol": {
            "unit": "one set of labels per image; duplicate boxes count once",
            "source_labels": SOURCE_LABELS,
            "exact_match": "predicted label set equals annotated label set within source scope",
            "box_reference_iou": MATCH_IOU,
            "zero_denominator_prf": 0.0,
            "note": "Metric change only; rescoring does not improve localization or model quality.",
        },
        "images": len(records),
        "review_failures": sum(bool(r.get("review_error")) for r in records),
        "confirmation": confirmation_stats,
        "variants": {
            variant: {
                level: _aggregate(rows, variant, level) for level in ("image_level", "box_level")
            }
            for variant in variants
        },
        "records": [{key: value for key, value in row.items() if key != "scores"} for row in rows],
    }


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--confirmation-state", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    inputs = [args.state] + ([args.confirmation_state] if args.confirmation_state else [])
    if args.output.resolve() in {path.resolve() for path in inputs}:
        parser.error("output must not overwrite an input state")
    try:
        source = json.loads(args.state.read_text(encoding="utf-8"))
        confirmation = (
            json.loads(args.confirmation_state.read_text(encoding="utf-8"))
            if args.confirmation_state
            else None
        )
        if not isinstance(source, dict) or (
            confirmation is not None and not isinstance(confirmation, dict)
        ):
            raise ValueError("input states must be JSON objects")
        report = evaluate_cached(source, confirmation)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    report["inputs"] = {
        "state": str(args.state.resolve()),
        "confirmation_state": str(args.confirmation_state.resolve())
        if args.confirmation_state
        else None,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Scored {report['images']} cached images: {args.output}")


if __name__ == "__main__":
    main()
