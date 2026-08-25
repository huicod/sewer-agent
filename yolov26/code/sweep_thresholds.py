from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class Detection:
    image_id: str
    cls: int
    conf: float
    box: tuple[float, float, float, float]


@dataclass
class GroundTruth:
    image_id: str
    cls: int
    box: tuple[float, float, float, float]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sweep deployment confidence thresholds on a YOLO validation split.")
    parser.add_argument("--model", type=Path, required=True, help="Path to best.pt.")
    parser.add_argument("--data", type=Path, default=Path("runs_data/sedd_yolo/sedd.yaml"))
    parser.add_argument("--split", choices=["val", "test"], default="val")
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--iou-match", type=float, default=0.5, help="IoU required to count a detection as TP.")
    parser.add_argument("--nms-iou", type=float, default=0.7, help="NMS IoU used during prediction.")
    parser.add_argument("--min-conf", type=float, default=0.05)
    parser.add_argument("--max-conf", type=float, default=0.95)
    parser.add_argument("--step", type=float, default=0.05)
    parser.add_argument("--device", default=None)
    parser.add_argument("--output", type=Path, default=Path("runs_eval/threshold_sweep_val.csv"))
    parser.add_argument("--workers", type=int, default=8)
    return parser.parse_args()


def yolo_to_xyxy(values: list[float]) -> tuple[float, float, float, float]:
    x, y, w, h = values
    return x - w / 2.0, y - h / 2.0, x + w / 2.0, y + h / 2.0


def iou_xyxy(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def load_dataset_yaml(path: Path) -> tuple[Path, dict[int, str], dict[str, str]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    root = Path(data["path"])
    names_raw = data["names"]
    names = {int(k): v for k, v in names_raw.items()} if isinstance(names_raw, dict) else dict(enumerate(names_raw))
    return root, names, data


def load_ground_truth(root: Path, data: dict[str, str], split: str) -> list[GroundTruth]:
    image_dir = root / data[split]
    label_dir = root / data[split].replace("images", "labels", 1)
    gts: list[GroundTruth] = []

    for image_path in sorted(image_dir.iterdir()):
        if image_path.is_dir():
            continue
        label_path = label_dir / f"{image_path.stem}.txt"
        if not label_path.exists():
            continue
        for line in label_path.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) != 5:
                continue
            cls = int(float(parts[0]))
            box = yolo_to_xyxy([float(x) for x in parts[1:]])
            gts.append(GroundTruth(image_path.stem, cls, box))
    return gts


def predict(model_path: Path, root: Path, data: dict[str, str], split: str, args: argparse.Namespace) -> list[Detection]:
    from ultralytics import YOLO

    source = root / data[split]
    model = YOLO(str(model_path))
    predict_kwargs = {
        "source": str(source),
        "imgsz": args.imgsz,
        "conf": args.min_conf,
        "iou": args.nms_iou,
        "stream": True,
        "verbose": False,
        "workers": args.workers,
    }
    if args.device is not None:
        predict_kwargs["device"] = args.device

    detections: list[Detection] = []
    for result in model.predict(**predict_kwargs):
        image_id = Path(result.path).stem
        if result.boxes is None:
            continue
        boxes = result.boxes.xyxyn.cpu().tolist()
        classes = result.boxes.cls.cpu().tolist()
        confs = result.boxes.conf.cpu().tolist()
        for box, cls, conf in zip(boxes, classes, confs):
            detections.append(Detection(image_id, int(cls), float(conf), tuple(float(x) for x in box)))
    return detections


def score_threshold(
    detections: list[Detection],
    gts: list[GroundTruth],
    class_ids: list[int],
    threshold: float,
    iou_match: float,
) -> dict[str, float]:
    gt_by_key: dict[tuple[str, int], list[GroundTruth]] = defaultdict(list)
    for gt in gts:
        gt_by_key[(gt.image_id, gt.cls)].append(gt)

    tp = fp = 0
    matched: set[tuple[str, int, int]] = set()

    for det in sorted((d for d in detections if d.conf >= threshold), key=lambda d: d.conf, reverse=True):
        candidates = gt_by_key.get((det.image_id, det.cls), [])
        best_iou, best_idx = 0.0, -1
        for idx, gt in enumerate(candidates):
            key = (gt.image_id, gt.cls, idx)
            if key in matched:
                continue
            overlap = iou_xyxy(det.box, gt.box)
            if overlap > best_iou:
                best_iou, best_idx = overlap, idx
        if best_iou >= iou_match and best_idx >= 0:
            tp += 1
            matched.add((det.image_id, det.cls, best_idx))
        else:
            fp += 1

    total_gt = sum(1 for gt in gts if gt.cls in class_ids)
    fn = total_gt - tp
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "threshold": threshold,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def frange(start: float, stop: float, step: float) -> list[float]:
    values = []
    cur = start
    while cur <= stop + 1e-9:
        values.append(round(cur, 4))
        cur += step
    return values


def main() -> None:
    args = parse_args()
    model_path = args.model.resolve()
    data_path = args.data.resolve()
    if not model_path.exists():
        raise FileNotFoundError(f"Model not found: {model_path}")
    if not data_path.exists():
        raise FileNotFoundError(f"Data yaml not found: {data_path}")

    root, names, data = load_dataset_yaml(data_path)
    gts = load_ground_truth(root, data, args.split)
    detections = predict(model_path, root, data, args.split, args)
    class_ids = sorted(names)

    rows = []
    for threshold in frange(args.min_conf, args.max_conf, args.step):
        row = score_threshold(detections, gts, class_ids, threshold, args.iou_match)
        rows.append(row)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    best_f1 = max(rows, key=lambda row: row["f1"])
    recall_candidates = [row for row in rows if row["recall"] >= 0.85]
    best_recall_guard = max(recall_candidates, key=lambda row: row["precision"]) if recall_candidates else None

    print(f"Wrote threshold sweep: {args.output}")
    print(f"Best F1: {best_f1}")
    if best_recall_guard:
        print(f"Best precision with recall >= 0.85: {best_recall_guard}")
    else:
        print("No threshold reached recall >= 0.85")


if __name__ == "__main__":
    main()
