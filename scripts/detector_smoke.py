from __future__ import annotations

import argparse
import json
from pathlib import Path
from time import perf_counter
from typing import Any

from pipe_inspector.vision.detector import UltralyticsDetector


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one explicit YOLO26 detector smoke inference."
    )
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--imgsz", type=int, default=960)
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def run_smoke(args: argparse.Namespace) -> dict[str, Any]:
    detector = UltralyticsDetector(
        args.checkpoint,
        device=args.device,
        imgsz=args.imgsz,
        conf=args.conf,
    )
    started = perf_counter()
    batch = detector.predict_image(args.image)
    elapsed_seconds = perf_counter() - started
    return {
        "checkpoint": str(args.checkpoint),
        "class_names": detector.class_names,
        "conf": args.conf,
        "detection_count": len(batch.detections),
        "device": args.device,
        "elapsed_seconds": elapsed_seconds,
        "image": str(args.image),
        "imgsz": args.imgsz,
        "result": batch.model_dump(mode="json"),
    }


def main() -> None:
    args = parse_args()
    payload = run_smoke(args)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if args.output is not None:
        args.output.write_text(f"{rendered}\n", encoding="utf-8")


if __name__ == "__main__":
    main()
