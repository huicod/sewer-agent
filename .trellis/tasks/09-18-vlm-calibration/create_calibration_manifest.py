"""Create a pending human calibration manifest from the validation split."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from pipe_inspector.evaluation.guided_calibration import build_calibration_manifest

NAMES = {0: "PL", 1: "BX", 2: "FS", 3: "BLOCK", 4: "AJ"}


def _labels(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [NAMES[int(line.split()[0])] for line in path.read_text().splitlines() if line.strip()]


def _detector_candidates(path: Path, *, width: int, height: int) -> list[dict]:
    if not path.exists():
        return []
    candidates = []
    for line in path.read_text(encoding="utf-8").splitlines():
        values = line.split()
        if len(values) < 6:
            raise ValueError(f"invalid prediction row: {path}")
        class_id, x, y, box_width, box_height, score = map(float, values[:6])
        candidates.append(
            {
                "label": NAMES[int(class_id)],
                "score": score,
                "bbox_xyxy": [
                    (x - box_width / 2) * width,
                    (y - box_height / 2) * height,
                    (x + box_width / 2) * width,
                    (y + box_height / 2) * height,
                ],
            }
        )
    return candidates


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--prediction-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-samples", type=int, default=60)
    args = parser.parse_args()
    rows = [
        row for row in csv.DictReader(args.manifest.open(encoding="utf-8")) if row["split"] == "val"
    ]
    candidates = [
        {
            "sample_id": row["id"],
            "group_id": row["group_id"],
            "dataset": row["dataset"],
            "image": row["image"],
            "width": int(row["width"]),
            "height": int(row["height"]),
            "labels": _labels(args.data_root / "labels" / "val" / f"{row['id']}.txt"),
            "detector_candidates": _detector_candidates(
                args.prediction_dir / "labels" / f"{row['id']}.txt",
                width=int(row["width"]),
                height=int(row["height"]),
            ),
            "vlm_review": None,
            "vlm_additions": [],
        }
        for row in rows
    ]
    samples = build_calibration_manifest(candidates, max_samples=args.max_samples)
    scoring_sidecar = {
        item["sample_id"]: {
            "dataset": item["dataset"],
            "group_id": item["group_id"],
            "labels": item.get("labels", []),
        }
        for item in samples
    }
    for item in samples:
        item.pop("labels", None)
    result = {
        "schema_version": "guided-human-calibration-v1",
        "protocol": {
            "source_split": "validation",
            "source_manifest": str(args.manifest),
            "group_disjoint": True,
            "human_decisions_required": True,
            "test_labels_used": False,
            "scoring_sidecar": str(args.output.with_name("calibration_scoring.json")),
        },
        "samples": samples,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    template = args.output.with_name("calibration_template.jsonl")
    template.write_text(
        "".join(json.dumps(sample, ensure_ascii=False) + "\n" for sample in samples),
        encoding="utf-8",
    )
    args.output.with_name("calibration_scoring.json").write_text(
        json.dumps(scoring_sidecar, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"created {len(samples)} pending calibration samples: {args.output}")


if __name__ == "__main__":
    main()
