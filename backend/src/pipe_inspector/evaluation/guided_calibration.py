"""Validation-only calibration helpers for detector protection thresholds."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from pipe_inspector.evaluation.review import LabeledBox, score_boxes

SCORED_LABELS = frozenset({"AJ", "BX", "FS", "PL", "BLOCK"})
SEDD_SCORED_LABELS = frozenset({"PL", "BLOCK"})
CALIBRATION_LABEL_ORDER = ("AJ", "BLOCK", "BX", "FS", "PL", "__empty__")


def _box(value: Mapping[str, Any]) -> LabeledBox:
    return LabeledBox.model_validate(
        {
            "label": value["label"],
            "bbox_xyxy": value["bbox_xyxy"],
            "score": value.get("score", 1.0),
        }
    )


def _scored_records(
    records: Sequence[Mapping[str, Any]], label: str
) -> list[tuple[list[LabeledBox], list[LabeledBox]]]:
    pairs = []
    for record in records:
        dataset = record.get("dataset")
        allowed = SEDD_SCORED_LABELS if dataset == "sedd" else SCORED_LABELS
        if label not in allowed:
            continue
        targets = [_box(item) for item in record.get("ground_truth", ())]
        predictions = [_box(item) for item in record.get("predictions", ())]
        pairs.append(
            (
                [item for item in targets if item.label.value == label],
                [item for item in predictions if item.label.value == label],
            )
        )
    return pairs


def _counts(
    records: Sequence[Mapping[str, Any]], label: str, threshold: float
) -> dict[str, int | float]:
    totals = {"tp": 0, "fp": 0, "fn": 0}
    for targets, predictions in _scored_records(records, label):
        scored = score_boxes(
            targets, [item for item in predictions if item.score >= threshold]
        )["micro"]
        for key in totals:
            totals[key] += int(scored[key])
    tp, fp, fn = (totals[key] for key in ("tp", "fp", "fn"))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return totals | {"precision": precision, "recall": recall, "f1": f1}


def calibrate_class_thresholds(
    records: Sequence[Mapping[str, Any]],
    *,
    labels: Sequence[str] = tuple(sorted(SCORED_LABELS)),
    thresholds: Sequence[float] = tuple(i / 100 for i in range(100)),
    target_precision: float = 0.75,
) -> dict[str, dict[str, Any]]:
    """Choose protection thresholds from validation records only.

    The primary objective is the highest recall among thresholds meeting the
    requested precision. If no threshold meets it, the result is explicitly a
    ``max_f1_fallback`` and must not be treated as calibrated to the target.
    """
    if not 0 < target_precision <= 1:
        raise ValueError("target_precision must be in (0, 1]")
    normalized = tuple(sorted({round(float(value), 6) for value in thresholds}))
    if not normalized or any(value < 0 or value > 1 for value in normalized):
        raise ValueError("thresholds must contain values in [0, 1]")
    result: dict[str, dict[str, Any]] = {}
    for label in labels:
        pairs = _scored_records(records, label)
        if not pairs:
            result[label] = {"status": "unscored_source"}
            continue
        candidates = [
            {"threshold": threshold, **_counts(records, label, threshold)}
            for threshold in normalized
        ]
        eligible = [item for item in candidates if item["precision"] >= target_precision]
        if eligible:
            selected = max(
                eligible,
                key=lambda item: (item["recall"], -item["threshold"], item["precision"]),
            )
            selection = "precision_target"
        else:
            selected = max(
                candidates,
                key=lambda item: (
                    item["f1"], item["precision"], item["recall"], -item["threshold"]
                ),
            )
            selection = "max_f1_fallback"
        result[label] = {
            "status": "scored",
            "selection": selection,
            "threshold": selected["threshold"],
            "target_precision": target_precision,
            "counts": {key: selected[key] for key in ("tp", "fp", "fn")},
            "precision": selected["precision"],
            "recall": selected["recall"],
            "f1": selected["f1"],
            "candidates": candidates,
        }
    return result


def build_calibration_manifest(
    candidates: Sequence[Mapping[str, Any]], *, max_samples: int = 60
) -> list[dict[str, Any]]:
    """Select a deterministic, group-disjoint validation calibration manifest."""
    if max_samples <= 0:
        raise ValueError("max_samples must be positive")
    buckets: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for candidate in sorted(candidates, key=lambda item: str(item["sample_id"])):
        labels = {str(label) for label in candidate.get("labels", ())}
        bucket = next((label for label in CALIBRATION_LABEL_ORDER if label in labels), "__empty__")
        buckets[bucket].append(candidate)
    selected: list[dict[str, Any]] = []
    used_groups: set[str] = set()
    while len(selected) < max_samples:
        progress = False
        for bucket in CALIBRATION_LABEL_ORDER:
            while buckets[bucket]:
                candidate = buckets[bucket].pop(0)
                group_id = str(candidate["group_id"])
                if group_id in used_groups:
                    continue
                used_groups.add(group_id)
                item = dict(candidate)
                item["labels"] = sorted(str(label) for label in candidate.get("labels", ()))
                item["human_review"] = {
                    "status": "pending",
                    "reviewer": None,
                    "expected_candidate_count": None,
                    "expected_addition_count": None,
                    "candidate_decisions": [],
                    "addition_decisions": [],
                    "note": "",
                }
                selected.append(item)
                progress = True
                break
            if len(selected) >= max_samples:
                break
        if not progress:
            break
    return selected


def summarize_human_calibration(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Summarize only explicit human decisions; pending records block claims."""
    incomplete = []
    for record in records:
        review = record.get("human_review", {})
        candidate_decisions = review.get("candidate_decisions", ())
        addition_decisions = review.get("addition_decisions", ())
        expected_candidates = review.get("expected_candidate_count")
        expected_additions = review.get("expected_addition_count")
        reviewer = review.get("reviewer")
        complete = (
            review.get("status") == "complete"
            and isinstance(reviewer, str)
            and bool(reviewer.strip())
            and isinstance(expected_candidates, int)
            and expected_candidates >= 0
            and isinstance(expected_additions, int)
            and expected_additions >= 0
            and len(candidate_decisions) == expected_candidates
            and len(addition_decisions) == expected_additions
            and all(isinstance(item.get("correct"), bool) for item in candidate_decisions)
            and all(isinstance(item.get("correct"), bool) for item in addition_decisions)
        )
        if not complete:
            incomplete.append(record.get("sample_id"))
    pending = len(incomplete)
    if pending:
        return {"status": "pending", "pending_images": pending, "can_claim_accuracy": False}
    candidate_decisions = [
        decision
        for record in records
        for decision in record.get("human_review", {}).get("candidate_decisions", ())
        if isinstance(decision.get("correct"), bool)
    ]
    addition_decisions = [
        decision
        for record in records
        for decision in record.get("human_review", {}).get("addition_decisions", ())
        if isinstance(decision.get("correct"), bool)
    ]
    candidate_correct = sum(bool(decision["correct"]) for decision in candidate_decisions)
    addition_correct = sum(bool(decision["correct"]) for decision in addition_decisions)
    candidate_precision = (
        candidate_correct / len(candidate_decisions) if candidate_decisions else None
    )
    addition_precision = addition_correct / len(addition_decisions) if addition_decisions else None
    by_verdict: dict[str, list[bool]] = defaultdict(list)
    for decision in candidate_decisions:
        by_verdict[str(decision.get("verdict", "unknown"))].append(bool(decision["correct"]))
    precision_by_verdict = {
        verdict: sum(values) / len(values) for verdict, values in sorted(by_verdict.items())
    }
    return {
        "status": "complete",
        "pending_images": 0,
        "can_claim_accuracy": True,
        "candidate_decisions": len(candidate_decisions),
        "candidate_correct": candidate_correct,
        "candidate_precision": candidate_precision,
        "candidate_precision_by_verdict": precision_by_verdict,
        "addition_decisions": len(addition_decisions),
        "addition_correct": addition_correct,
        "addition_precision": addition_precision,
        "precision_by_action": precision_by_verdict | {"addition": addition_precision},
    }
