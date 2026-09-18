"""No-provider replay of saved YOLO-guided VLM reviews."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pipe_inspector.agent.guided_review import GuidedReview
from pipe_inspector.contracts.inspection import ImageObservation
from pipe_inspector.evaluation.guided import FUSION_VERSION, fuse_guided
from pipe_inspector.evaluation.review import LABELS, LabeledBox, score_boxes


def _allowed(dataset: str | None) -> set[str]:
    return {"PL", "BLOCK"} if dataset == "sedd" else set(LABELS)


def _validated_boxes(
    items: Sequence[Mapping[str, Any]], dataset: str | None
) -> tuple[list[LabeledBox], int]:
    allowed = _allowed(dataset)
    selected: list[LabeledBox] = []
    ignored = 0
    for item in items:
        box = LabeledBox.model_validate(
            {
                "label": item["label"],
                "bbox_xyxy": item["bbox_xyxy"],
                "score": item.get("score", 1.0),
            }
        )
        if box.label.value not in allowed:
            ignored += 1
        else:
            selected.append(box)
    return selected, ignored


def _rates(counts: Mapping[str, int]) -> dict[str, int | float]:
    tp, fp, fn = (int(counts[key]) for key in ("tp", "fp", "fn"))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
    }


def _merge_scores(scores: Sequence[dict[str, Any]]) -> dict[str, Any]:
    totals = {label: Counter(tp=0, fp=0, fn=0) for label in LABELS}
    for score in scores:
        for label in LABELS:
            for key in ("tp", "fp", "fn"):
                totals[label][key] += int(score["per_class"][label][key])
    per_class = {label: _rates(totals[label]) for label in LABELS}
    micro = _rates(
        {
            key: sum(int(per_class[label][key]) for label in LABELS)
            for key in ("tp", "fp", "fn")
        }
    )
    return {"micro": micro, "per_class": per_class}


def replay_state(
    state: Mapping[str, Any], *, policy: str = FUSION_VERSION
) -> dict[str, Any]:
    """Apply a fusion policy to saved observations/reviews without provider access."""
    records = state.get("records")
    if not isinstance(records, list):
        raise ValueError("saved state records must be a list")

    score_sets: dict[str, list[dict[str, Any]]] = {
        "raw_detector": [],
        "saved_predictions": [],
        "replayed_predictions": [],
    }
    replayed_records: list[dict[str, Any]] = []
    action_counts: Counter[str] = Counter()
    ignored_predictions = Counter()
    review_errors = 0
    pending_changes = 0

    for record in records:
        observation = ImageObservation.model_validate(record["observation"])
        review = (
            GuidedReview.model_validate(record["review"])
            if record.get("review") is not None and not record.get("review_error")
            else None
        )
        fusion = fuse_guided(observation, review, policy=policy)
        dataset = record.get("dataset")
        targets, ignored_target = _validated_boxes(record.get("ground_truth", ()), dataset)
        raw, ignored_raw = _validated_boxes(
            [item.model_dump(mode="json") for item in observation.detection.detections],
            dataset,
        )
        saved, ignored_saved = _validated_boxes(record.get("predictions", ()), dataset)
        replayed, ignored_replayed = _validated_boxes(fusion["predictions"], dataset)
        ignored_predictions.update(
            {
                "ground_truth": ignored_target,
                "raw_detector": ignored_raw,
                "saved_predictions": ignored_saved,
                "replayed_predictions": ignored_replayed,
            }
        )
        score_sets["raw_detector"].append(score_boxes(targets, raw))
        score_sets["saved_predictions"].append(score_boxes(targets, saved))
        score_sets["replayed_predictions"].append(score_boxes(targets, replayed))
        action_counts.update(change["action"] for change in fusion["changes"])
        pending_changes += sum(bool(change.get("pending")) for change in fusion["changes"])
        review_errors += int(record.get("review_error") is not None)
        replayed_records.append(
            {
                "sample_id": record.get("sample_id"),
                "dataset": dataset,
                "predictions": fusion["predictions"],
                "changes": fusion["changes"],
            }
        )

    return {
        "policy": policy,
        "provider_attempts": 0,
        "image_count": len(records),
        "review_errors": review_errors,
        "pending_change_count": pending_changes,
        "action_counts": dict(sorted(action_counts.items())),
        "ignored_unscored": dict(ignored_predictions),
        "raw_detector": _merge_scores(score_sets["raw_detector"]),
        "saved_predictions": _merge_scores(score_sets["saved_predictions"]),
        "summary": _merge_scores(score_sets["replayed_predictions"]),
        "records": replayed_records,
    }


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay saved guided reviews without a VLM API")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--policy", default=FUSION_VERSION)
    args = parser.parse_args()
    state = json.loads(args.input.read_text(encoding="utf-8"))
    result = replay_state(state, policy=args.policy)
    _write_json(args.output, result)
    print(
        json.dumps(
            {key: result[key] for key in ("policy", "provider_attempts", "summary")},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
