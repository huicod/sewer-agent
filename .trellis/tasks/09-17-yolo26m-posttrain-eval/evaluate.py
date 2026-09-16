"""One fixed post-training evaluation; no training, API calls or deployment."""

from __future__ import annotations

import csv
import gc
import html
import json
import os
import shutil
import time
from collections import Counter
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "8")
os.environ.setdefault("MKL_NUM_THREADS", "8")

import numpy as np
import torch
from PIL import Image, ImageDraw
from pipe_inspector.evaluation.review import LabeledBox, score_boxes
from pipe_inspector.vision.detector import UltralyticsDetector
from ultralytics import YOLO

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "var/datasets/merged_five_v1"
OUT = ROOT / "var/evaluations/2026-09-17-yolo26m"
NEW = ROOT / "var/training/merged_five_yolo26m_960_5090/weights/best.pt"
OLD = ROOT / "yolov26/experiments/sewer2403_5class_yolo26s_img960_e100/weights/best.pt"
NAMES = {0: "PL", 1: "BX", 2: "FS", 3: "BLOCK", 4: "AJ"}
COLORS = {"PL": "#ff5050", "BX": "#00cfff", "FS": "#ffbb00", "BLOCK": "#d070ff", "AJ": "#30ee80"}


def json_default(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(type(value).__name__)


def save(path, value):
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False, default=json_default)
        + "\n",
        encoding="utf-8",
    )


def rates(counts):
    tp, fp, fn = (counts[k] for k in ("tp", "fp", "fn"))
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return dict(counts, precision=p, recall=r, f1=2 * p * r / (p + r) if p + r else 0.0)


def overlay(source, boxes, destination, title):
    image = Image.open(source).convert("RGB")
    draw = ImageDraw.Draw(image)
    for box in boxes:
        color = COLORS[box.label.value]
        x1, y1, x2, y2 = box.bbox_xyxy
        draw.rectangle((x1, y1, x2, y2), outline=color, width=max(2, image.width // 400))
        label = f"{box.label.value} {box.score:.2f}"
        y = max(15, y1 - 13)
        draw.rectangle((x1, y, min(image.width, x1 + 80), y + 13), fill="black")
        draw.text((x1 + 1, y), label, fill=color)
    draw.rectangle((0, 0, min(image.width, 440), 14), fill="black")
    draw.text((2, 1), title, fill="white")
    image.save(destination, quality=90)


def main():
    if (OUT / "fixed_summary.json").exists():
        raise RuntimeError("Completed output exists; do not silently overwrite a fixed experiment")
    OUT.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(8)
    torch.cuda.set_per_process_memory_fraction(
        30 * 1024**3 / torch.cuda.get_device_properties(0).total_memory
    )
    with (DATA / "split_manifest.csv").open() as handle:
        all_rows = list(csv.DictReader(handle))
    rows = sorted((r for r in all_rows if r["split"] == "test"), key=lambda r: r["id"])
    assert len(rows) == 606
    assert Counter(r["dataset"] for r in rows) == {"sewer2403": 562, "sedd": 44}
    assert not (
        {r["group_id"] for r in rows} & {r["group_id"] for r in all_rows if r["split"] != "test"}
    )
    save(
        OUT / "protocol.json",
        {
            "new_checkpoint": str(NEW),
            "old_checkpoint": str(OLD),
            "dataset": str(DATA),
            "imgsz": 960,
            "fixed_conf": 0.25,
            "match_iou": 0.5,
            "mAP_conf_floor": 0.001,
            "mAP_batch": 16,
            "half": False,
            "fixed_inference": "production UltralyticsDetector.predict_image, one image at a time",
            "old_reference_only": (
                "Old model may have trained on new sewer holdout; "
                "not a clean generalization comparison"
            ),
            "sedd_scope": ["PL", "BLOCK"],
            "threshold_or_checkpoint_selection_on_test": False,
        },
    )

    for name, data_yaml, split, classes in [
        ("new_mixed_val", "data.yaml", "val", None),
        ("new_mixed_test", "data.yaml", "test", None),
        ("new_sewer_test", "eval_sewer2403.yaml", "test", None),
        ("new_sedd_test", "eval_sedd.yaml", "test", [0, 3]),
    ]:
        model = YOLO(str(NEW))
        assert model.names == NAMES
        seen = []
        model.add_callback("on_val_end", lambda validator: seen.append(validator.seen))
        metrics = model.val(
            data=str(DATA / data_yaml),
            split=split,
            imgsz=960,
            batch=16,
            conf=0.001,
            iou=0.7,
            device="0",
            workers=4,
            half=False,
            classes=classes,
            plots=True,
            save_txt=True,
            save_conf=True,
            project=str(OUT),
            name=name,
            exist_ok=False,
            verbose=True,
        )
        save(
            OUT / f"{name}.json",
            {
                "split": split,
                "data": data_yaml,
                "classes": classes,
                "metrics": metrics.results_dict,
                "per_class": metrics.summary(),
                "speed": metrics.speed,
                "seen": seen[-1],
            },
        )
        del metrics, model
        gc.collect()
        torch.cuda.empty_cache()

    records = {}
    for row in rows:
        w, h = int(row["width"]), int(row["height"])
        targets = []
        for line in (DATA / "labels/test" / f"{row['id']}.txt").read_text().splitlines():
            c, x, y, bw, bh = map(float, line.split())
            targets.append(
                LabeledBox(
                    label=NAMES[int(c)],
                    bbox_xyxy=(
                        (x - bw / 2) * w,
                        (y - bh / 2) * h,
                        (x + bw / 2) * w,
                        (y + bh / 2) * h,
                    ),
                )
            )
        records[row["id"]] = {
            "id": row["id"],
            "dataset": row["dataset"],
            "image": str(DATA / row["image"]),
            "group_id": row["group_id"],
            "ground_truth": [b.model_dump(mode="json") for b in targets],
        }

    performance = {}
    for name, checkpoint in (("new", NEW), ("old", OLD)):
        detector = UltralyticsDetector(checkpoint, device="0", imgsz=960, conf=0.25)
        detector.load()
        assert set(detector.class_names.values()) == set(NAMES.values())
        detector.warmup(records[rows[0]["id"]]["image"])
        elapsed = []
        for index, row in enumerate(rows):
            record = records[row["id"]]
            started = time.perf_counter()
            result = detector.predict_image(record["image"])
            elapsed.append(time.perf_counter() - started)
            assert (result.width, result.height) == (int(row["width"]), int(row["height"]))
            predictions = [
                LabeledBox(label=d.label, bbox_xyxy=d.bbox_xyxy, score=d.score)
                for d in result.detections
            ]
            allowed = {"PL", "BLOCK"} if row["dataset"] == "sedd" else set(NAMES.values())
            included = [b for b in predictions if b.label.value in allowed]
            targets = [LabeledBox.model_validate(b) for b in record["ground_truth"]]
            record[name] = {
                "result": result.model_dump(mode="json"),
                "score": score_boxes(targets, included),
                "excluded_unlabeled_predictions": len(predictions) - len(included),
            }
            if index % 100 == 0:
                print(f"FIXED {name}: {index + 1}/{len(rows)}", flush=True)
        # Small multi-image call validates the same adapter's batch contract, not accuracy equality.
        batch_paths = [records[r["id"]]["image"] for r in rows[:4]]
        batches = detector.predict_batch(batch_paths)
        assert len(batches) == 4
        save(OUT / f"{name}_adapter_batch_smoke.json", [b.model_dump(mode="json") for b in batches])
        performance[name] = {
            "median_ms": float(np.median(elapsed) * 1000),
            "p95_ms": float(np.percentile(elapsed, 95) * 1000),
            "images": len(elapsed),
            "class_names": detector.class_names,
            "batch_contract": "passed: 4 images",
            "precision": "FP32",
        }
        detector.unload()
        assert detector._model is None
        del detector
        gc.collect()
        torch.cuda.empty_cache()
        performance[name]["allocated_after_unload_mib"] = torch.cuda.memory_allocated() / 1024**2
        save(OUT / "per_image.json", list(records.values()))

    summary = {}
    for source in ("mixed", "sewer2403", "sedd"):
        subset = [r for r in records.values() if source == "mixed" or r["dataset"] == source]
        summary[source] = {"images": len(subset)}
        for name in ("new", "old"):
            counts = Counter({k: 0 for k in ("tp", "fp", "fn")})
            per_class = {}
            for label in NAMES.values():
                if source == "sedd" and label not in {"PL", "BLOCK"}:
                    continue
                c = {
                    key: sum(r[name]["score"]["per_class"][label][key] for r in subset)
                    for key in ("tp", "fp", "fn")
                }
                counts.update(c)
                per_class[label] = rates(c)
            summary[source][name] = {
                "micro": rates(counts),
                "per_class": per_class,
                "exact_images": sum(
                    r[name]["score"]["micro"]["fp"] == r[name]["score"]["micro"]["fn"] == 0
                    for r in subset
                ),
                "ignored_unlabeled_predictions": sum(
                    r[name]["excluded_unlabeled_predictions"] for r in subset
                ),
            }
    save(OUT / "fixed_summary.json", summary)
    save(OUT / "adapter_performance.json", performance)

    gallery = OUT / "gallery"
    gallery.mkdir(exist_ok=True)
    index_lines = []
    for record in records.values():
        item = gallery / record["id"]
        item.mkdir(exist_ok=True)
        shutil.copy2(record["image"], item / "original.jpg")
        overlay(
            record["image"],
            [LabeledBox.model_validate(b) for b in record["ground_truth"]],
            item / "ground_truth.jpg",
            "Ground truth (SEDD: PL/BLOCK labels only)",
        )
        for name in ("old", "new"):
            boxes = [
                LabeledBox(label=b["label"], bbox_xyxy=b["bbox_xyxy"], score=b["score"])
                for b in record[name]["result"]["detections"]
            ]
            overlay(
                record["image"], boxes, item / f"{name}.jpg", f"{name} YOLO26: conf>=0.25 (no VLM)"
            )
        save(item / "result.json", record)
        details = "".join(
            f"<p>{name}: {html.escape(str(record[name]['score']['micro']))}</p>"
            for name in ("old", "new")
        )
        item.joinpath("index.html").write_text(
            "<meta charset='utf-8'><style>img{width:48%;vertical-align:top}</style>"
            + f"<h2>{html.escape(record['id'])}</h2>"
            + "<p>IoU≥0.5, conf≥0.25. SEDD仅评价PL/BLOCK；其他类别预测展示但不计错。</p>"
            + details
            + "".join(
                f"<img src='{s}.jpg' title='{s}'>"
                for s in ("original", "ground_truth", "old", "new")
            )
            + "<p><a href='result.json'>原始框/分数/匹配统计 JSON</a></p>",
            encoding="utf-8",
        )
        counts = record["new"]["score"]["micro"]
        index_lines.append(
            f"<tr><td><a href='{record['id']}/index.html'>{record['id']}</a></td>"
            f"<td>{counts['tp']}</td><td>{counts['fp']}</td><td>{counts['fn']}</td></tr>"
        )
    gallery.joinpath("index.html").write_text(
        "<meta charset='utf-8'><h1>全部606张固定测试图</h1>"
        "<p>每项包含原图、GT、旧s、新m；conf=0.25，无VLM。"
        "旧s有sewer训练集重叠风险。SEDD仅评价PL/BLOCK。</p>"
        "<table><tr><th>样本</th><th>新TP</th><th>新FP</th><th>新FN</th></tr>"
        + "".join(index_lines)
        + "</table>",
        encoding="utf-8",
    )
    save(
        OUT / "completion.json",
        {"images": len(records), "gallery_pages": len(index_lines), "status": "completed"},
    )
    print("EVALUATION COMPLETED", flush=True)


if __name__ == "__main__":
    main()
