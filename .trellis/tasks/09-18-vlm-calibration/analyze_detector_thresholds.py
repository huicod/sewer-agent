"""Analyze detector protection thresholds from an existing validation export."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

from pipe_inspector.evaluation.guided_calibration import calibrate_class_thresholds

NAMES = {0: "PL", 1: "BX", 2: "FS", 3: "BLOCK", 4: "AJ"}


def _read_boxes(path: Path, *, width: int, height: int, prediction: bool) -> list[dict]:
    if not path.exists():
        return []
    boxes = []
    for line in path.read_text(encoding="utf-8").splitlines():
        values = line.split()
        minimum = 6 if prediction else 5
        if len(values) < minimum:
            raise ValueError(f"invalid {'prediction' if prediction else 'label'} row: {path}")
        class_id, x, y, box_width, box_height = map(float, values[:5])
        item = {
            "label": NAMES[int(class_id)],
            "bbox_xyxy": [
                (x - box_width / 2) * width,
                (y - box_height / 2) * height,
                (x + box_width / 2) * width,
                (y + box_height / 2) * height,
            ],
        }
        if prediction:
            item["score"] = float(values[5])
        boxes.append(item)
    return boxes


def analyze(
    *, data_root: Path, prediction_dir: Path, manifest_path: Path, target_precision: float
) -> dict:
    rows = [
        row for row in csv.DictReader(manifest_path.open(encoding="utf-8")) if row["split"] == "val"
    ]
    records = []
    for row in rows:
        width, height = int(row["width"]), int(row["height"])
        records.append(
            {
                "sample_id": row["id"],
                "dataset": row["dataset"],
                "ground_truth": _read_boxes(
                    data_root / "labels" / "val" / f"{row['id']}.txt",
                    width=width,
                    height=height,
                    prediction=False,
                ),
                "predictions": _read_boxes(
                    prediction_dir / "labels" / f"{row['id']}.txt",
                    width=width,
                    height=height,
                    prediction=True,
                ),
            }
        )
    result = calibrate_class_thresholds(records, target_precision=target_precision)
    return {
        "protocol": {
            "split": "val",
            "data_root": str(data_root),
            "prediction_dir": str(prediction_dir),
            "manifest": str(manifest_path),
            "target_precision": target_precision,
            "match_iou": 0.5,
            "test_labels_used": False,
        },
        "images": len(records),
        "source_counts": dict(Counter(row["dataset"] for row in rows)),
        "thresholds": result,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--prediction-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target-precision", type=float, default=0.75)
    args = parser.parse_args()
    result = analyze(
        data_root=args.data_root,
        prediction_dir=args.prediction_dir,
        manifest_path=args.manifest,
        target_precision=args.target_precision,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result["thresholds"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
