"""Independently re-check VLM-only additions from the bounded calibration run.

This is an evaluation-only experiment.  It submits one fresh guided review per
proposed addition with a synthetic low-confidence candidate and never changes
the online conservative fusion policy.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

from pipe_inspector.agent.guided_review import GuidedReview
from pipe_inspector.config import Settings
from pipe_inspector.evaluation.guided_run import build_live_reviewer
from pipe_inspector.evaluation.review import LabeledBox, score_boxes
from pipe_inspector.evaluation.vlm_calibration_run import (
    independent_confirmation_accepts,
    reconstruct_observation,
)

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SOURCE = ROOT / "var/evaluations/2026-09-18-vlm-calibration/live-60-conf025"
DEFAULT_OUTPUT = (
    ROOT / "var/evaluations/2026-09-18-vlm-calibration/live-60-conf025-addition-confirmation"
)
DATA_ROOT = ROOT / "var/datasets/merged_five_v1"


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def _rates(counts: Counter[str]) -> dict[str, int | float]:
    tp, fp, fn = (int(counts[key]) for key in ("tp", "fp", "fn"))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1}


def _score_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    labels_by_source = {
        "sewer2403": {"AJ", "BX", "FS", "PL", "BLOCK"},
        "sedd": {"PL", "BLOCK"},
    }
    result: dict[str, Any] = {}
    for source in ("sewer2403", "sedd", "mixed"):
        subset = records if source == "mixed" else [r for r in records if r["dataset"] == source]
        labels = labels_by_source["sewer2403" if source == "mixed" else source]
        totals: Counter[str] = Counter()
        per_class: dict[str, Any] = {}
        for label in sorted(labels):
            counts: Counter[str] = Counter()
            for record in subset:
                counts.update(record["scores"]["confirmed"]["per_class"][label])
            per_class[label] = _rates(counts)
            totals.update(counts)
        result[source] = {
            "images": len(subset),
            "micro": _rates(totals),
            "per_class": per_class,
        }
    return result


def _proposal_key(sample_id: str, index: int) -> str:
    return f"{sample_id}::{index}"


def _proposal_records(source: dict[str, Any]) -> list[dict[str, Any]]:
    proposals: list[dict[str, Any]] = []
    for record in source.get("records", []):
        for index, item in enumerate(record.get("experimental_additions", [])):
            proposals.append(
                {
                    "key": _proposal_key(record["sample_id"], index),
                    "sample_id": record["sample_id"],
                    "dataset": record["dataset"],
                    "image": record["image"],
                    "width": record["observation"]["detection"]["width"],
                    "height": record["observation"]["detection"]["height"],
                    "proposal": item,
                }
            )
    return proposals


async def run(source_dir: Path, output_dir: Path, *, retry_errors: bool = False) -> None:
    source = _read_json(source_dir / "state.json")
    proposals = _proposal_records(source)
    state_path = output_dir / "state.json"
    if state_path.exists():
        state = _read_json(state_path)
    else:
        state = {
            "schema_version": "vlm-calibration-addition-confirmation-v1",
            "source_run": str(source_dir),
            "protocol": {
                "fresh_request_per_addition": True,
                "synthetic_candidate_score": 0.01,
                "accept_rule": "accept + original_presence=present + evidence_strength=clear",
                "promotion": "diagnostic_only; no online policy change",
            },
            "records": [],
        }
    completed = {
        item["key"]
        for item in state.get("records", [])
        if not (retry_errors and item.get("review_error"))
    }
    settings = Settings()
    reviewer, provider = build_live_reviewer(settings, output_dir)
    state["provider"] = provider
    by_sample = {item["sample_id"]: item for item in source.get("records", [])}
    for index, proposal in enumerate(proposals, start=1):
        if proposal["key"] in completed:
            continue
        source_record = by_sample[proposal["sample_id"]]
        image_path = DATA_ROOT / proposal["image"]
        observation = reconstruct_observation(
            proposal["width"],
            proposal["height"],
            [{
                "label": proposal["proposal"]["label"],
                "score": 0.01,
                "bbox_xyxy": proposal["proposal"]["bbox_xyxy"],
            }],
        )
        reviewer.start_sample(proposal["key"])
        review: GuidedReview | None = None
        error: str | None = None
        started = time.perf_counter()
        try:
            review = GuidedReview.model_validate(await reviewer.review(image_path, observation))
        except Exception as exc:  # persist only a safe status
            error = getattr(exc, "code", "unexpected_error")
        result = {
            "key": proposal["key"],
            "sample_id": proposal["sample_id"],
            "dataset": proposal["dataset"],
            "image": proposal["image"],
            "proposal": proposal["proposal"],
            "review": review.model_dump(mode="json") if review is not None else None,
            "review_error": error,
            "accepted": bool(
                review is not None
                and independent_confirmation_accepts(review, proposal["proposal"]["label"])
            ),
            "duration_seconds": time.perf_counter() - started,
            "source_record_present": bool(source_record),
        }
        if retry_errors:
            state["records"] = [item for item in state["records"] if item["key"] != proposal["key"]]
        state["records"].append(result)
        _write_json(state_path, state)
        print(f"{index}/{len(proposals)} completed; error={error or 'none'}", flush=True)

    accepted_by_key = {
        item["key"]
        for item in state["records"]
        if item.get("accepted") and not item.get("review_error")
    }
    scored_records: list[dict[str, Any]] = []
    for record in source.get("records", []):
        raw = [LabeledBox.model_validate(item) for item in record["conservative_predictions"]]
        additions = [
            LabeledBox.model_validate(item)
            for index, item in enumerate(record.get("experimental_additions", []))
            if _proposal_key(record["sample_id"], index) in accepted_by_key
        ]
        ground_truth = [LabeledBox.model_validate(item) for item in record["ground_truth"]]
        confirmed = [*raw, *additions]
        scored_records.append(
            {
                "dataset": record["dataset"],
                "scores": {"confirmed": score_boxes(ground_truth, confirmed)},
            }
        )
    summary = {
        "schema_version": "vlm-calibration-addition-confirmation-summary-v1",
        "source_run": str(source_dir),
        "proposals": len(proposals),
        "confirmation_records": len(state["records"]),
        "review_errors": sum(bool(item.get("review_error")) for item in state["records"]),
        "accepted_additions": len(accepted_by_key),
        "accepted_by_label": dict(
            Counter(
                item["proposal"]["label"]
                for item in state["records"]
                if item.get("accepted") and not item.get("review_error")
            )
        ),
        "metrics": _score_metrics(scored_records),
        "promotion": "diagnostic_only; no online policy change",
    }
    _write_json(output_dir / "summary.json", summary)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--retry-errors", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.source, args.output, retry_errors=args.retry_errors))


if __name__ == "__main__":
    main()
