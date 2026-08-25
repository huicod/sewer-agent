from __future__ import annotations

import argparse
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train YOLO26 detection on the converted SEDD dataset.")
    parser.add_argument("--data", type=Path, default=Path("runs_data/sedd_yolo/sedd.yaml"))
    parser.add_argument("--model", default="yolo26n.pt", help="YOLO26 weight, e.g. yolo26n.pt/yolo26s.pt/yolo26m.pt.")
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=int, default=-1, help="-1 lets Ultralytics auto-select batch size.")
    parser.add_argument("--device", default=None, help="Examples: 0, cpu. Defaults to Ultralytics auto selection.")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--project", default="runs_detect")
    parser.add_argument("--name", default=None)
    parser.add_argument("--patience", type=int, default=30)
    parser.add_argument("--cache", action="store_true", help="Cache images if RAM/disk budget allows.")
    parser.add_argument("--pretrained", action="store_true", default=True)
    parser.add_argument("--no-plots", action="store_true", help="Disable Ultralytics metric plots.")
    parser.add_argument("--degrees", type=float, default=None, help="Random rotation range in degrees.")
    parser.add_argument("--translate", type=float, default=None, help="Random translation fraction.")
    parser.add_argument("--scale", type=float, default=None, help="Random scale gain.")
    parser.add_argument("--mosaic", type=float, default=None, help="Mosaic augmentation probability.")
    parser.add_argument("--close-mosaic", type=int, default=None, help="Disable mosaic for the final N epochs.")
    parser.add_argument("--hsv-h", type=float, default=None, help="HSV hue augmentation gain.")
    parser.add_argument("--hsv-s", type=float, default=None, help="HSV saturation augmentation gain.")
    parser.add_argument("--hsv-v", type=float, default=None, help="HSV value augmentation gain.")
    parser.add_argument("--fliplr", type=float, default=None, help="Horizontal flip probability.")
    parser.add_argument("--flipud", type=float, default=None, help="Vertical flip probability.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    data_path = args.data.resolve()
    if not data_path.exists():
        raise FileNotFoundError(f"Data yaml not found: {data_path}. Run prepare_sedd_yolo.py first.")

    from ultralytics import YOLO

    run_name = args.name or f"sedd_{Path(args.model).stem}_img{args.imgsz}"
    model = YOLO(args.model)
    train_kwargs = {
        "data": str(data_path),
        "epochs": args.epochs,
        "imgsz": args.imgsz,
        "batch": args.batch,
        "workers": args.workers,
        "project": str(Path(args.project).resolve()),
        "name": run_name,
        "patience": args.patience,
        "cache": args.cache,
        "pretrained": args.pretrained,
        "plots": not args.no_plots,
    }
    if args.device is not None:
        train_kwargs["device"] = args.device
    for key in (
        "degrees",
        "translate",
        "scale",
        "mosaic",
        "close_mosaic",
        "hsv_h",
        "hsv_s",
        "hsv_v",
        "fliplr",
        "flipud",
    ):
        value = getattr(args, key)
        if value is not None:
            train_kwargs[key] = value

    model.train(**train_kwargs)


if __name__ == "__main__":
    main()
