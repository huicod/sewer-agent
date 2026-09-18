"""Run one resumable, validation-only guided VLM calibration cohort."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pipe_inspector.agent.guided_fusion import fuse_guided
from pipe_inspector.agent.guided_review import GuidedReview
from pipe_inspector.config import Settings
from pipe_inspector.evaluation.gallery import export_gallery
from pipe_inspector.evaluation.guided_run import build_live_reviewer
from pipe_inspector.evaluation.review import (
    EvaluationManifest,
    EvaluationSample,
    LabeledBox,
    _matched_pairs,
    score_boxes,
)
from pipe_inspector.evaluation.vlm_calibration_run import (
    experimental_addition_boxes,
    filter_candidates,
    reconstruct_observation,
)
from pipe_inspector.vision.quality import analyze_quality

ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = ROOT / "var/datasets/merged_five_v1"
PREDICTION_DIR = ROOT / "var/evaluations/2026-09-17-yolo26m/new_mixed_val"
DEFAULT_MANIFEST = ROOT / "var/evaluations/2026-09-18-vlm-calibration/calibration_manifest.json"
DEFAULT_OUTPUT = ROOT / "var/evaluations/2026-09-18-vlm-calibration/live-60"
NAMES = {0: "PL", 1: "BX", 2: "FS", 3: "BLOCK", 4: "AJ"}
ALL_LABELS = set(NAMES.values())
SEDD_LABELS = {"PL", "BLOCK"}


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _read_boxes(path: Path, *, width: int, height: int, with_score: bool) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    boxes = []
    for line in path.read_text(encoding="utf-8").splitlines():
        values = line.split()
        if len(values) < (6 if with_score else 5):
            raise ValueError(f"invalid detector row: {path}")
        class_id, x, y, box_width, box_height = map(float, values[:5])
        box: dict[str, Any] = {
            "label": NAMES[int(class_id)],
            "bbox_xyxy": [
                max(0.0, min(float(width), (x - box_width / 2) * width)),
                max(0.0, min(float(height), (y - box_height / 2) * height)),
                max(0.0, min(float(width), (x + box_width / 2) * width)),
                max(0.0, min(float(height), (y + box_height / 2) * height)),
            ],
        }
        if with_score:
            box["score"] = float(values[5])
        boxes.append(box)
    return boxes


def _ground_truth(sample: Mapping[str, Any]) -> list[LabeledBox]:
    return [
        LabeledBox.model_validate(item)
        for item in _read_boxes(
            DATA_ROOT / "labels" / "val" / f"{sample['sample_id']}.txt",
            width=int(sample["width"]),
            height=int(sample["height"]),
            with_score=False,
        )
    ]


def _as_boxes(items: Sequence[Mapping[str, Any]], dataset: str) -> list[LabeledBox]:
    allowed = SEDD_LABELS if dataset == "sedd" else ALL_LABELS
    return [
        LabeledBox.model_validate(item)
        for item in items
        if str(item["label"]) in allowed
    ]


def _metrics(records: Sequence[Mapping[str, Any]], key: str) -> dict[str, Any]:
    sources: dict[str, list[dict[str, Any]]] = {"sewer2403": [], "sedd": [], "mixed": []}
    for record in records:
        sources[record["dataset"]].append(record)
        sources["mixed"].append(record)
    result = {}
    for source, subset in sources.items():
        labels = ALL_LABELS if source != "sedd" else SEDD_LABELS
        per_class: dict[str, Any] = {}
        totals = Counter(tp=0, fp=0, fn=0)
        for label in sorted(labels):
            counts = Counter()
            for record in subset:
                counts.update(record["scores"][key]["per_class"][label])
            per_class[label] = _rates(counts)
            totals.update(counts)
        result[source] = {"images": len(subset), "micro": _rates(totals), "per_class": per_class}
    return result


def _rates(counts: Mapping[str, int]) -> dict[str, int | float]:
    tp, fp, fn = (int(counts[key]) for key in ("tp", "fp", "fn"))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1}


def _rows(manifest_path: Path) -> list[dict[str, Any]]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows = payload.get("samples")
    if not isinstance(rows, list) or not rows:
        raise ValueError("calibration manifest has no samples")
    return rows


def _manifest(rows: Sequence[Mapping[str, Any]]) -> EvaluationManifest:
    samples = []
    for row in rows:
        image_path = DATA_ROOT / row["image"]
        samples.append(
            EvaluationSample(
                sample_id=row["sample_id"],
                group_id=row["group_id"],
                image_path=image_path.resolve(),
                width=int(row["width"]),
                height=int(row["height"]),
                ground_truth=tuple(_ground_truth(row)),
            )
        )
    return EvaluationManifest(
        dataset_name="merged_five_v1_validation_calibration",
        split_provenance="validation split; 60 group-disjoint samples",
        samples=tuple(samples),
    )


def _record_for(
    row: Mapping[str, Any],
    observation: Any,
    review: GuidedReview | None,
    error: str | None,
):
    fusion = fuse_guided(observation, review)
    additions = experimental_addition_boxes(observation, review) if review is not None else []
    experimental = [
        *[LabeledBox.model_validate(item) for item in fusion["predictions"]],
        *additions,
    ]
    raw = [
        LabeledBox(
            label=item.label,
            bbox_xyxy=item.bbox_xyxy,
            score=item.score,
        )
        for item in observation.detection.detections
    ]
    ground_truth = _ground_truth(row)
    dataset = row["dataset"]
    views = {
        "raw": _as_boxes([item.model_dump(mode="json") for item in raw], dataset),
        "conservative": _as_boxes(fusion["predictions"], dataset),
        "experimental": _as_boxes(
            [item.model_dump(mode="json") for item in experimental], dataset
        ),
    }
    scores = {
        name: score_boxes(ground_truth, boxes)
        for name, boxes in views.items()
    }
    pairs_raw = {target for _, target in _matched_pairs(ground_truth, views["raw"])}
    pairs_experimental = {
        target for _, target in _matched_pairs(ground_truth, views["experimental"])
    }
    return {
        "sample_id": row["sample_id"],
        "dataset": dataset,
        "image": row["image"],
        "observation": observation.model_dump(mode="json"),
        "review": review.model_dump(mode="json") if review is not None else None,
        "review_error": error,
        "predictions": [box.model_dump(mode="json") for box in views["experimental"]],
        "conservative_predictions": [box.model_dump(mode="json") for box in views["conservative"]],
        "experimental_additions": [box.model_dump(mode="json") for box in additions],
        "changes": fusion["changes"],
        "ground_truth": [box.model_dump(mode="json") for box in ground_truth],
        "scores": scores,
        "attribution": {
            "raw_matched": len(pairs_raw),
            "experimental_matched": len(pairs_experimental),
            "recovered_raw_misses": len(pairs_experimental - pairs_raw),
            "experimental_addition_count": len(additions),
        },
    }


async def run(
    manifest_path: Path,
    output: Path,
    *,
    retry_errors: bool = False,
    limit: int | None = None,
) -> None:
    rows = _rows(manifest_path)
    if limit is not None:
        if limit <= 0:
            raise ValueError("limit must be positive")
        rows = rows[:limit]
    settings = Settings()
    reviewer, provider = build_live_reviewer(settings, output)
    state_path = output / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {
        "schema_version": "vlm-calibration-live-v1",
        "manifest": str(manifest_path),
        "sample_ids": [row["sample_id"] for row in rows],
        "provider": provider,
        "protocol": {
            "detector_conf": 0.25,
            "prediction_export": str(PREDICTION_DIR),
            "prompt_version": "yolo-guided-review-v4",
            "addition_projection": "single-call-model-confirmed-diagnostic-only",
        },
        "records": [],
    }
    completed = {
        item["sample_id"]
        for item in state["records"]
        if not (retry_errors and item.get("review_error"))
    }
    for index, row in enumerate(rows, start=1):
        if row["sample_id"] in completed:
            continue
        image_path = DATA_ROOT / row["image"]
        candidates = _read_boxes(
            PREDICTION_DIR / "labels" / f"{row['sample_id']}.txt",
            width=int(row["width"]),
            height=int(row["height"]),
            with_score=True,
        )
        candidates = filter_candidates(candidates, min_score=0.25)
        observation = reconstruct_observation(
            int(row["width"]),
            int(row["height"]),
            candidates,
            quality=analyze_quality(image_path),
        )
        reviewer.start_sample(row["sample_id"])
        review = None
        error = None
        started = time.perf_counter()
        try:
            review = GuidedReview.model_validate(await reviewer.review(image_path, observation))
        except Exception as exc:  # persist only a safe status, never provider text
            error = getattr(exc, "code", "unexpected_error")
        record = _record_for(row, observation, review, error)
        record["duration_seconds"] = time.perf_counter() - started
        if retry_errors:
            state["records"] = [
                item for item in state["records"] if item["sample_id"] != row["sample_id"]
            ]
        state["records"].append(record)
        _write_json(state_path, state)
        print(f"{index}/{len(rows)} completed; error={error or 'none'}", flush=True)
    summary = {
        "schema_version": "vlm-calibration-summary-v1",
        "manifest": str(manifest_path),
        "images": len(state["records"]),
        "review_errors": sum(bool(item.get("review_error")) for item in state["records"]),
        "provider_attempts": len(list((output / "requests").glob("*/request.json"))),
        "metrics": _metrics(state["records"], "raw"),
        "conservative_metrics": _metrics(state["records"], "conservative"),
        "experimental_metrics": _metrics(state["records"], "experimental"),
        "attribution": {
            "recovered_raw_misses": sum(
                item["attribution"]["recovered_raw_misses"] for item in state["records"]
            ),
            "experimental_additions": sum(
                item["attribution"]["experimental_addition_count"] for item in state["records"]
            ),
        },
        "promotion": "diagnostic_only; no online policy change",
    }
    _write_json(output / "summary.json", summary)
    export_gallery(_manifest(rows), state["records"], output / "gallery")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--retry-errors", action="store_true")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    asyncio.run(run(args.manifest, args.output, retry_errors=args.retry_errors, limit=args.limit))


if __name__ == "__main__":
    main()
