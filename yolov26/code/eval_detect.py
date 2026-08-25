from __future__ import annotations

import argparse
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate a trained YOLO26 detector on val or test split.")
    parser.add_argument("--model", type=Path, required=True, help="Path to best.pt or another YOLO checkpoint.")
    parser.add_argument("--data", type=Path, default=Path("runs_data/sedd_yolo/sedd.yaml"))
    parser.add_argument("--split", choices=["val", "test"], default="test")
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--conf", type=float, default=0.001, help="Validation confidence threshold used by Ultralytics.")
    parser.add_argument("--iou", type=float, default=0.7, help="NMS IoU threshold.")
    parser.add_argument("--device", default=None)
    parser.add_argument("--project", default="runs_eval")
    parser.add_argument("--name", default=None)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--no-plots", action="store_true")
    parser.add_argument("--save-json", action="store_true", help="Save COCO-style JSON predictions if supported.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    model_path = args.model.resolve()
    data_path = args.data.resolve()
    if not model_path.exists():
        raise FileNotFoundError(f"Model not found: {model_path}")
    if not data_path.exists():
        raise FileNotFoundError(f"Data yaml not found: {data_path}")

    from ultralytics import YOLO

    run_name = args.name or f"{model_path.stem}_{args.split}_img{args.imgsz}"
    model = YOLO(str(model_path))
    val_kwargs = {
        "data": str(data_path),
        "split": args.split,
        "imgsz": args.imgsz,
        "conf": args.conf,
        "iou": args.iou,
        "workers": args.workers,
        "project": args.project,
        "name": run_name,
        "plots": not args.no_plots,
        "save_json": args.save_json,
    }
    if args.device is not None:
        val_kwargs["device"] = args.device

    metrics = model.val(**val_kwargs)
    box = metrics.box
    print("Evaluation summary")
    print(f"  split: {args.split}")
    print(f"  mAP50-95(B): {box.map:.6f}")
    print(f"  mAP50(B):    {box.map50:.6f}")
    print(f"  mAP75(B):    {box.map75:.6f}")
    print(f"  per-class AP50-95: {box.maps}")


if __name__ == "__main__":
    main()
