"""One-off, local YOLO diagnostic. No review clients, workflow starts or API calls."""

import argparse
import csv
import html
import importlib.util
import json
import random
import shutil
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

from PIL import Image
from pipe_inspector.evaluation.review import LabeledBox, _rates, score_boxes
from pipe_inspector.vision.detector import UltralyticsDetector

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "var/evaluations/2026-09-16-sedd-yolo"
SEDD = ROOT / "var/datasets/sedd_external"
SEWER = ROOT / "var/datasets/sewer2403_source/extracted/市政下水雨污管道缺陷数据2403"
WEIGHT = ROOT / "yolov26/experiments/sewer2403_5class_yolo26s_img960_e100/weights/best.pt"
LABELS = ("PL", "BX", "FS", "BLOCK", "AJ")
THRESHOLDS = (0.25, 0.1, 0.05, 0.01, 0.001)
UPLOADS = ("b01a0463-3f77-481d-8d73-20987adc72c5", "2341c137-41c2-4414-a373-05d0409c6515")


def write_json(path, payload, *, derived=False):
    # Raw cohorts/predictions are immutable; derived summaries may be regenerated offline.
    with path.open("w" if derived else "x", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")


def xml_sample(xml, image, mapping, dataset):
    tree = ET.parse(xml).getroot()
    w, h = int(tree.findtext("size/width")), int(tree.findtext("size/height"))
    with Image.open(image) as im:
        if im.size != (w, h):
            raise ValueError("dimensions")
    raw = []
    for obj in tree.findall("object"):
        box = [float(obj.findtext("bndbox/" + key)) for key in ("xmin", "ymin", "xmax", "ymax")]
        if not (0 <= box[0] < box[2] <= w and 0 <= box[1] < box[3] <= h):
            raise ValueError("box_bounds")
        raw.append({"label": obj.findtext("name").strip(), "bbox_xyxy": box})
    return {
        "sample_id": dataset + "-" + xml.stem,
        "dataset": dataset,
        "image_path": str(image),
        "xml_path": str(xml),
        "width": w,
        "height": h,
        "source_objects": raw,
        "ground_truth": [dict(x, label=mapping[x["label"]]) for x in raw if x["label"] in mapping],
    }


def prepare():
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / "manifest.json").exists():
        raise FileExistsError("manifest already exists; reuse without changing the cohort")
    rows, rejects = [], Counter()
    for xml in sorted((SEDD / "bbox").glob("*.xml")):
        image = SEDD / "image" / (xml.stem + ".jpg")
        if ET.parse(xml).getroot().findtext("filename") != image.name:
            rejects["filename_mismatch"] += 1
            continue
        try:
            rows.append(xml_sample(xml, image, {"crack": "PL", "deposit": "BLOCK"}, "sedd"))
        except (ValueError, OSError) as exc:
            rejects[str(exc)] += 1
    spec = importlib.util.spec_from_file_location(
        "sewer_prep", ROOT / "yolov26/code/prepare_sewer2403_yolo.py"
    )
    prep = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = prep
    spec.loader.exec_module(prep)
    buckets = {x: [] for x in (*LABELS, "OTHER")}
    for xml in sorted((SEWER / "labels").glob("*.xml")):
        image = SEWER / "images" / (xml.stem + ".jpg")
        if not image.exists():
            continue
        try:
            row = xml_sample(xml, image, prep.FIVE_CLASS_MAPPING, "sewer2403")
        except (ValueError, OSError):
            continue
        source = {x["label"] for x in row["source_objects"]}
        target = {x["label"] for x in row["ground_truth"]}
        if len(target) == 1 and source <= set(prep.FIVE_CLASS_MAPPING):
            stratum = next(iter(target))
        elif not target and source:
            stratum = "OTHER"
        else:
            continue
        row["group_id"] = prep.group_key(image, "video")
        row["stratum"] = stratum
        buckets[stratum].append(row)
    rng, used, source_counts = random.Random(20260916), set(), {}
    for label, candidates in buckets.items():
        rng.shuffle(candidates)
        selected = []
        for row in candidates:
            if row["group_id"] in used:
                continue
            selected.append(row)
            used.add(row["group_id"])
            if len(selected) == 40:
                break
        if len(selected) < 40:
            raise ValueError(f"not enough independent source groups for {label}: {len(selected)}")
        rows.extend(selected)
        source_counts[label] = len(selected)
    payload = {
        "seed": 20260916,
        "sedd_rejected": dict(rejects),
        "source_strata": source_counts,
        "mapping": {
            "sedd": {"crack": "PL", "deposit": "BLOCK", "root": "unscored OTHER"},
            "sewer2403": prep.FIVE_CLASS_MAPPING,
        },
        "limitations": [
            "SEDD scoring restricted to annotated PL/BLOCK; FS/BX/AJ unscored",
            "Unknown source/video grouping in SEDD; not independent-frame statistics",
            "Sewer2403 source-domain sample may overlap training; not held-out accuracy",
            "Root/OTHER images are not known normal; no VLM/LLM adjudication",
        ],
        "samples": rows,
    }
    write_json(OUT / "manifest.json", payload)
    print(
        json.dumps(
            {
                "sedd": sum(x["dataset"] == "sedd" for x in rows),
                "sewer2403": source_counts,
                "rejected": dict(rejects),
            }
        ),
        flush=True,
    )


def boxes(items):
    return [
        LabeledBox(label=x["label"], bbox_xyxy=x["bbox_xyxy"], score=x.get("score", 1))
        for x in items
    ]


def score(row, threshold, iou=0.5):
    scope = ("PL", "BLOCK") if row["dataset"] == "sedd" else LABELS
    return score_boxes(
        boxes(row["ground_truth"]),
        boxes([x for x in row["predictions"] if x["label"] in scope and x["score"] >= threshold]),
        iou_threshold=iou,
    )


def run():
    rows = json.loads((OUT / "manifest.json").read_text())["samples"]
    result_path = OUT / "predictions.jsonl"
    if result_path.exists():
        raise FileExistsError("predictions already exist; refusing to overwrite/repeat")
    d = UltralyticsDetector(WEIGHT, device="0", imgsz=960, conf=0.001)
    d.load()
    import ultralytics

    write_json(
        OUT / "runtime.json",
        {
            "weight": str(WEIGHT),
            "class_names": d.class_names,
            "model_version": d.model_version,
            "ultralytics": ultralytics.__version__,
            "imgsz": 960,
            "inference_conf": 0.001,
            "thresholds": THRESHOLDS,
            "batch_size": 1,
            "local_gpu_only": True,
            "llm_calls": 0,
            "vlm_calls": 0,
        },
    )
    # Reuse existing tested matching; cheap deterministic guard before real inference.
    b = LabeledBox(label="PL", bbox_xyxy=(1, 1, 9, 9))
    assert score_boxes([b], [b])["micro"]["tp"] == 1
    assert score_boxes([b], [b, b])["micro"]["fp"] == 1
    started = time.perf_counter()
    with result_path.open("x") as f:
        for i, row in enumerate(rows):
            t = time.perf_counter()
            prediction = d.predict_image(row["image_path"])
            result = dict(
                row,
                predictions=[x.model_dump(mode="json") for x in prediction.detections],
                elapsed_seconds=time.perf_counter() - t,
            )
            f.write(json.dumps(result, ensure_ascii=False) + "\n")
            f.flush()
            if (i + 1) % 100 == 0:
                print(
                    f"{i + 1}/{len(rows)} images; "
                    f"{time.perf_counter() - started:.1f}s; API calls=0",
                    flush=True,
                )
    write_json(OUT / "timing.json", {"images": len(rows), "seconds": time.perf_counter() - started})
    # Exact user inputs: native output vs adapter at the deployed threshold, then size probes.
    case_rows = []
    for asset in UPLOADS:
        path = ROOT / "var/assets" / asset / "1/source"
        with Image.open(path) as im:
            w, h = im.size
        case = {
            "sample_id": "upload-" + asset,
            "dataset": "upload",
            "image_path": str(path),
            "width": w,
            "height": h,
            "ground_truth": [],
            "source_objects": [],
            "probes": [],
        }
        for size in (640, 960, 1280):
            d.imgsz, d.conf = size, 0.001
            batch = d.predict_image(path)
            case["probes"].append(
                {
                    "imgsz": size,
                    "predictions": [x.model_dump(mode="json") for x in batch.detections],
                }
            )
            if size == 960:
                case["predictions"] = case["probes"][-1]["predictions"]
        d.imgsz, d.conf = 960, 0.25
        adapter = d.predict_image(path)
        native = d._model.predict(
            source=[str(path)], device="0", imgsz=960, conf=0.25, verbose=False
        )[0]
        case["adapter_count_025"] = len(adapter.detections)
        case["native_count_025"] = len(native.boxes)
        case["native_matches_adapter"] = d._translate_result(native) == adapter
        assert case["native_matches_adapter"]
        assert [x.model_dump(mode="json") for x in adapter.detections] == [
            x for x in case["predictions"] if x["score"] >= 0.25
        ]
        case_rows.append(case)
    write_json(OUT / "uploaded-cases.json", case_rows)
    d.unload()


def summarize():
    records = [json.loads(line) for line in (OUT / "predictions.jsonl").read_text().splitlines()]
    expected = json.loads((OUT / "manifest.json").read_text())["samples"]
    assert [x["sample_id"] for x in records] == [x["sample_id"] for x in expected]
    summaries = {}
    for dataset in ("sedd", "sewer2403"):
        subset = [x for x in records if x["dataset"] == dataset]
        scope = ("PL", "BLOCK") if dataset == "sedd" else LABELS
        summary = {
            "images": len(subset),
            "scored_labels": scope,
            "unscored_labels": [x for x in LABELS if x not in scope],
            "gt_counts": dict(Counter(x["label"] for row in subset for x in row["ground_truth"])),
            "thresholds": {},
        }
        for threshold in THRESHOLDS:
            by_class = {x: Counter() for x in LABELS}
            no_detections, raw_labels, positive_hits = 0, Counter(), 0
            for row in subset:
                pred = [x for x in row["predictions"] if x["score"] >= threshold]
                no_detections += not pred
                raw_labels.update(x["label"] for x in pred)
                result = score(row, threshold)
                positive_hits += result["micro"]["tp"] > 0
                for label, counts in result["per_class"].items():
                    by_class[label].update({k: counts[k] for k in ("tp", "fp", "fn")})
            totals = sum(by_class.values(), Counter())
            summary["thresholds"][str(threshold)] = {
                "micro": _rates(**{k: totals[k] for k in ("tp", "fp", "fn")}),
                "per_class": {
                    k: _rates(**{x: v[x] for x in ("tp", "fp", "fn")})
                    for k, v in by_class.items()
                    if k in scope
                },
                "matched_scope_pl_block": _rates(
                    **{
                        key: sum(by_class[label][key] for label in ("PL", "BLOCK"))
                        for key in ("tp", "fp", "fn")
                    }
                ),
                "images_with_no_predictions_any_class": no_detections,
                "all_raw_class_counts": dict(raw_labels),
                "images_with_at_least_one_matched_target": positive_hits,
            }
        # Relaxed overlap is a diagnostic only, not the headline metric.
        counts = Counter()
        for row in subset:
            counts.update(
                {k: v for k, v in score(row, 0.25, 0.1)["micro"].items() if k in ("tp", "fp", "fn")}
            )
        summary["relaxed_iou_01_conf_025"] = _rates(**counts)
        summaries[dataset] = summary
    write_json(OUT / "summary.json", summaries, derived=True)
    with (OUT / "per-image.csv").open("w") as f:
        writer = csv.writer(f)
        writer.writerow(
            (
                "sample_id",
                "dataset",
                "threshold",
                "gt",
                "all_predictions",
                "tp",
                "fp_scoped",
                "fn",
                "image_path",
            )
        )
        for row in records:
            for threshold in THRESHOLDS:
                s = score(row, threshold)["micro"]
                writer.writerow(
                    (
                        row["sample_id"],
                        row["dataset"],
                        threshold,
                        len(row["ground_truth"]),
                        sum(x["score"] >= threshold for x in row["predictions"]),
                        s["tp"],
                        s["fp"],
                        s["fn"],
                        row["image_path"],
                    )
                )
    # Explicitly selected diagnostic examples, not an accuracy sample.
    chosen = json.loads((OUT / "uploaded-cases.json").read_text())
    uploaded_analysis = []
    for case, source_id in zip(chosen, ("sedd-00002", "sedd-00016"), strict=True):
        source = next(x for x in records if x["sample_id"] == source_id)
        assert Path(case["image_path"]).read_bytes() == Path(source["image_path"]).read_bytes()
        case.update(
            source_sample_id=source_id,
            ground_truth=source["ground_truth"],
            source_objects=source["source_objects"],
            xml_path=source["xml_path"],
        )
        uploaded_analysis.append(
            dict(
                case,
                scores_by_size={
                    str(probe["imgsz"]): {
                        str(t): score(dict(case, predictions=probe["predictions"]), t)
                        for t in THRESHOLDS
                    }
                    for probe in case["probes"]
                },
            )
        )
    write_json(OUT / "uploaded-case-analysis.json", uploaded_analysis, derived=True)
    for dataset, label in (
        ("sedd", "PL"),
        ("sedd", "BLOCK"),
        ("sewer2403", "PL"),
        ("sewer2403", "FS"),
        ("sewer2403", "BLOCK"),
    ):
        for hit in (False, True):
            for row in records:
                if (
                    row["dataset"] == dataset
                    and any(x["label"] == label for x in row["ground_truth"])
                    and bool(score(row, 0.25)["per_class"][label]["tp"]) == hit
                ):
                    chosen.append(row)
                    break
    gallery = OUT / "gallery"
    gallery.mkdir(exist_ok=True)
    links = []
    seen = set()
    for row in chosen:
        if row["sample_id"] in seen:
            continue
        seen.add(row["sample_id"])
        folder = gallery / row["sample_id"]
        folder.mkdir(exist_ok=True)
        shutil.copyfile(row["image_path"], folder / "original.jpg")
        write_json(folder / "result.json", row, derived=True)
        panels = []
        for name, items, color in (
            ("ground_truth", row["ground_truth"], "#00ffff"),
            ("yolo_025", [x for x in row["predictions"] if x["score"] >= 0.25], "#ff4040"),
            ("yolo_005", [x for x in row["predictions"] if x["score"] >= 0.05], "#ffb000"),
        ):
            svg = [
                '<svg xmlns="http://www.w3.org/2000/svg" '
                f'viewBox="0 0 {row["width"]} {row["height"]}">'
                f'<image href="original.jpg" width="{row["width"]}" height="{row["height"]}"/>'
            ]
            for item in items:
                x, y, x2, y2 = item["bbox_xyxy"]
                label = html.escape(
                    item["label"] + (f" {item['score']:.3f}" if "score" in item else " GT")
                )
                svg.append(
                    f'<rect x="{x}" y="{y}" width="{x2 - x}" height="{y2 - y}" '
                    f'fill="none" stroke="{color}" stroke-width="3"/>'
                    f'<text x="{x}" y="{max(20, y)}" fill="{color}" '
                    f'font-size="22">{label}</text>'
                )
            svg.append("</svg>")
            (folder / (name + ".svg")).write_text("".join(svg))
            note = (
                "No target GT; not necessarily normal"
                if name == "ground_truth" and not row["ground_truth"]
                else f"{len(items)} boxes"
            )
            panels.append(f"<section><h3>{name}: {note}</h3>{''.join(svg)}</section>")
        (folder / "index.html").write_text(
            f'<meta charset="utf-8"><title>{row["sample_id"]}</title><h2>{row["sample_id"]}</h2>'
            '<p>Diagnostic examples. No VLM/LLM called. <a href="result.json">Raw result</a></p>'
            "<style>body{background:#171b22;color:white;font-family:sans-serif}"
            "main{display:flex}section{width:33%}svg{width:100%}</style>"
            f"<main>{''.join(panels)}</main>"
        )
        links.append(f'<li><a href="{row["sample_id"]}/index.html">{row["sample_id"]}</a></li>')
    (gallery / "index.html").write_text(
        '<meta charset="utf-8"><h1>YOLO-only diagnostic examples</h1>'
        "<p>Source annotations / conf 0.25 / conf 0.05. "
        "Uploaded files match SEDD 00002 and 00016; annotations are not human-corrected. "
        "Examples deliberately include hits and misses; "
        "use summary.json for cohort metrics.</p><ul>" + "".join(links) + "</ul>"
    )
    print(json.dumps(summaries, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("prepare", "run", "summarize"))
    args = parser.parse_args()
    {"prepare": prepare, "run": run, "summarize": summarize}[args.mode]()
