"""Full frozen-test comparison of independent VLM and YOLO-guided VLM.

This script is deliberately resumable: each provider response (or safe error)
is checkpointed before the next image. It never changes model/data assets or
calls the text-only LLM.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import html
import json
import shutil
import time
from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw
from pipe_inspector.agent.api_client import APIClientError, OpenAICompatibleClient
from pipe_inspector.agent.guided_fusion import FUSION_VERSION, fuse_guided
from pipe_inspector.agent.guided_review import GUIDED_PROMPT_VERSION, GuidedImageReviewer
from pipe_inspector.agent.image_discovery import IndependentImageReviewer
from pipe_inspector.config import Settings
from pipe_inspector.contracts.detection import DetectionBatch, DetectionLabel
from pipe_inspector.contracts.inspection import ImageObservation
from pipe_inspector.evaluation.guided_run import EvidenceCapturingClient
from pipe_inspector.evaluation.review import LabeledBox, score_boxes
from pipe_inspector.vision.detector import UltralyticsDetector
from pipe_inspector.vision.quality import analyze_quality

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "var/datasets/merged_five_v1"
OUT = ROOT / "var/evaluations/2026-09-18-vlm-yolo26m-comparison"
NEW = ROOT / "var/training/merged_five_yolo26m_960_5090/weights/best.pt"
LABELS = tuple(label.value for label in DetectionLabel)
COLORS = {"PL": "#ff5050", "BX": "#00cfff", "FS": "#ffbb00", "BLOCK": "#d070ff", "AJ": "#30ee80"}


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def safe_error(error: Exception) -> str:
    return error.code if isinstance(error, APIClientError) else "unexpected_error"


def gt_for(row: dict[str, str]) -> list[LabeledBox]:
    width, height = int(row["width"]), int(row["height"])
    result = []
    for line in (DATA / "labels/test" / f"{row['id']}.txt").read_text().splitlines():
        class_id, x, y, box_width, box_height = map(float, line.split())
        result.append(
            LabeledBox(
                label={
                    0: DetectionLabel.PL,
                    1: DetectionLabel.BX,
                    2: DetectionLabel.FS,
                    3: DetectionLabel.BLOCK,
                    4: DetectionLabel.AJ,
                }[int(class_id)],
                bbox_xyxy=(
                    (x - box_width / 2) * width,
                    (y - box_height / 2) * height,
                    (x + box_width / 2) * width,
                    (y + box_height / 2) * height,
                ),
            )
        )
    return result


def empty_observation(row: dict[str, str]) -> ImageObservation:
    return ImageObservation(
        detection=DetectionBatch(
            detector_id="vlm-only-input",
            model_version="none",
            label_space_version="sewer2403-5class-v1",
            width=int(row["width"]),
            height=int(row["height"]),
            detections=(),
        ),
        quality=analyze_quality(DATA / row["image"]),
    )


def known_vlm_boxes(review: Any) -> list[LabeledBox]:
    boxes = []
    for anomaly in review.additional_anomalies if review is not None else ():
        if anomaly.label not in LABELS or anomaly.bbox_xyxy is None:
            continue
        boxes.append(LabeledBox(label=anomaly.label, bbox_xyxy=anomaly.bbox_xyxy, score=1.0))
    return boxes


def allowed_boxes(boxes: list[LabeledBox], dataset: str) -> tuple[list[LabeledBox], int]:
    allowed = {"PL", "BLOCK"} if dataset == "sedd" else set(LABELS)
    selected = [box for box in boxes if box.label.value in allowed]
    return selected, len(boxes) - len(selected)


def score_record(row: dict[str, str], boxes: list[LabeledBox]) -> dict[str, Any]:
    targets = gt_for(row)
    selected, ignored = allowed_boxes(boxes, row["dataset"])
    return {"score": score_boxes(targets, selected), "ignored_unscored": ignored}


def provider_components(output: Path, guided: bool):
    settings = Settings()
    if not settings.vlm_configured:
        raise RuntimeError("VLM configuration is incomplete")
    provider = {
        "base_url": settings.vlm_base_url,
        "model": settings.vlm_model,
        "api_enable_thinking": settings.api_enable_thinking,
        "api_max_output_tokens": settings.api_max_output_tokens,
        "api_timeout_seconds": settings.api_timeout_seconds,
        "vlm_max_image_bytes": settings.vlm_max_image_bytes,
    }
    client = OpenAICompatibleClient(
        settings.vlm_base_url,
        settings.vlm_model,
        settings.vlm_api_key,
        timeout_seconds=settings.api_timeout_seconds,
        enable_thinking=settings.api_enable_thinking,
        max_output_tokens=settings.api_max_output_tokens,
    )
    captured = EvidenceCapturingClient(
        client,
        output / "requests",
        provider_settings=provider,
    )
    reviewer = GuidedImageReviewer(captured, max_image_bytes=settings.vlm_max_image_bytes)
    if not guided:
        reviewer = IndependentImageReviewer(captured, max_image_bytes=settings.vlm_max_image_bytes)
    return reviewer, captured, provider


def draw_boxes(source: Path, boxes: list[LabeledBox], destination: Path, title: str) -> None:
    image = Image.open(source).convert("RGB")
    draw = ImageDraw.Draw(image)
    for box in boxes:
        x1, y1, x2, y2 = box.bbox_xyxy
        color = COLORS[box.label.value]
        draw.rectangle((x1, y1, x2, y2), outline=color, width=max(2, image.width // 400))
        label = f"{box.label.value} {box.score:.2f}"
        top = max(0, y1 - 14)
        draw.rectangle((x1, top, min(image.width, x1 + 88), top + 14), fill="black")
        draw.text((x1 + 1, top + 1), label, fill=color)
    draw.rectangle((0, 0, min(image.width, 500), 15), fill="black")
    draw.text((2, 1), title, fill="white")
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, quality=90)


def build_rows() -> list[dict[str, str]]:
    with (DATA / "split_manifest.csv").open(encoding="utf-8") as handle:
        rows = sorted(
            (row for row in csv.DictReader(handle) if row["split"] == "test"),
            key=lambda r: r["id"],
        )
    expected_sources = {"sewer2403": 562, "sedd": 44}
    if len(rows) != 606 or Counter(row["dataset"] for row in rows) != expected_sources:
        raise RuntimeError("frozen test manifest is not the expected 562+44 cohort")
    return rows


async def run_group(
    name: str,
    rows: list[dict[str, str]],
    *,
    guided: bool,
    retry_errors: bool = False,
) -> None:
    output = OUT / name
    output.mkdir(parents=True, exist_ok=True)
    state_path = output / "state.json"
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("sample_ids") != [row["id"] for row in rows]:
            raise RuntimeError(f"{name}: frozen sample identities differ")
    else:
        state = {
            "schema_version": "vlm-two-group-v1",
            "group": name,
            "guided": guided,
            "sample_ids": [row["id"] for row in rows],
            "records": [],
        }
        write_json(state_path, state)

    if retry_errors:
        retry_records = [record for record in state["records"] if record.get("review_error")]
        history = state.setdefault("retry_history", [])
        history.extend(
            {
                "sample_id": record["sample_id"],
                "previous_error": record["review_error"],
                "previous_duration_seconds": record.get("duration_seconds"),
            }
            for record in retry_records
            if not any(
                item.get("sample_id") == record["sample_id"]
                and item.get("previous_error") == record["review_error"]
                for item in history
            )
        )
    completed = {
        record["sample_id"]
        for record in state["records"]
        if not (retry_errors and record.get("review_error"))
    }
    reviewer, captured, provider = provider_components(output, guided)
    detector = None
    if guided:
        detector = UltralyticsDetector(NEW, device="0", imgsz=960, conf=0.25)
        detector.load()
        if detector.class_names != {0: "PL", 1: "BX", 2: "FS", 3: "BLOCK", 4: "AJ"}:
            raise RuntimeError(f"unexpected new checkpoint class map: {detector.class_names}")

    state["provider"] = {key: value for key, value in provider.items()}
    state["protocol"] = {
        "imgsz": 960,
        "detector_conf": 0.25 if guided else None,
        "match_iou": 0.5,
        "sewer_labels": list(LABELS),
        "sedd_scored_labels": ["PL", "BLOCK"],
        "checkpoint": str(NEW) if guided else None,
        "prompt_mode": GUIDED_PROMPT_VERSION if guided else "image-discovery-v2",
        "one_provider_attempt_per_uncached_image": True,
    }
    write_json(state_path, state)

    for index, row in enumerate(rows, start=1):
        if row["id"] in completed:
            continue
        path = DATA / row["image"]
        observation = detector_batch = None
        if guided:
            detector_batch = detector.predict_image(path)
            observation = ImageObservation(detection=detector_batch, quality=analyze_quality(path))
        else:
            observation = empty_observation(row)
        captured.start_sample(row["id"])
        review = None
        review_error = None
        started = time.perf_counter()
        try:
            review = await reviewer.review(path, observation)
        except Exception as error:
            review_error = safe_error(error)
        duration = time.perf_counter() - started
        raw_boxes = []
        fused_boxes = []
        if guided:
            raw_boxes = [
                LabeledBox(label=item.label, bbox_xyxy=item.bbox_xyxy, score=item.score)
                for item in observation.detection.detections
            ]
            fusion = fuse_guided(observation, review)
            fused_boxes = [LabeledBox.model_validate(item) for item in fusion["predictions"]]
            changes = fusion["changes"]
        else:
            fused_boxes = known_vlm_boxes(review)
            changes = []
        selected, ignored = allowed_boxes(fused_boxes, row["dataset"])
        record = {
            "sample_id": row["id"],
            "dataset": row["dataset"],
            "image": row["image"],
            "width": int(row["width"]),
            "height": int(row["height"]),
            "ground_truth": [box.model_dump(mode="json") for box in gt_for(row)],
            "observation": observation.model_dump(mode="json"),
            "review": review.model_dump(mode="json") if review is not None else None,
            "review_error": review_error,
            "raw_detector_boxes": [box.model_dump(mode="json") for box in raw_boxes],
            "predictions": [box.model_dump(mode="json") for box in fused_boxes],
            "scored_predictions": [box.model_dump(mode="json") for box in selected],
            "ignored_unscored": ignored,
            "changes": changes,
            "score": score_boxes(gt_for(row), selected),
            "duration_seconds": duration,
        }
        if retry_errors:
            state["records"] = [
                item for item in state["records"] if item["sample_id"] != row["id"]
            ]
        state["records"].append(record)
        write_json(state_path, state)
        if index == 1 or index % 10 == 0:
            errors = sum(bool(item.get("review_error")) for item in state["records"])
            print(f"{name}: {index}/{len(rows)} complete; errors={errors}", flush=True)
    if detector is not None:
        detector.unload()
    write_json(output / "summary.json", summarize(state["records"], rows, name))
    render_gallery(output, state["records"], rows, name)


def rates(counts: dict[str, int]) -> dict[str, int | float]:
    tp, fp, fn = (counts[key] for key in ("tp", "fp", "fn"))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {
        **counts,
        "precision": precision,
        "recall": recall,
        "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
    }


def summarize(
    records: list[dict[str, Any]], rows: list[dict[str, str]], name: str
) -> dict[str, Any]:
    by_id = {record["sample_id"]: record for record in records}
    result: dict[str, Any] = {
        "group": name,
        "expected_images": len(rows),
        "evaluated_images": len(by_id),
        "missing_images": [row["id"] for row in rows if row["id"] not in by_id],
        "review_failures": sum(bool(record.get("review_error")) for record in records),
        "provider_attempts": len(list((OUT / name / "requests").glob("*/request.json"))),
        "sources": {},
    }
    row_by_id = {row["id"]: row for row in rows}
    for source in ("sewer2403", "sedd", "mixed"):
        subset = [record for record in records if source == "mixed" or record["dataset"] == source]
        scored_labels = set(LABELS) if source != "sedd" else {"PL", "BLOCK"}
        per_class = {}
        total = Counter()
        for label in sorted(scored_labels):
            count = Counter()
            for record in subset:
                per = record["score"]["per_class"][label]
                count.update({key: int(per[key]) for key in ("tp", "fp", "fn")})
            per_class[label] = rates(dict(count))
            total.update(count)
        source_result = {
            "images": len(subset),
            "micro": rates(dict(total)),
            "per_class": per_class,
            "ignored_unscored_predictions": sum(record["ignored_unscored"] for record in subset),
            "review_failures": sum(bool(record.get("review_error")) for record in subset),
        }
        if name == "yolo26m_guided":
            raw_total = Counter()
            raw_per_class = {}
            for label in sorted(scored_labels):
                count = Counter()
                for record in subset:
                    raw_boxes = [
                        LabeledBox.model_validate(item) for item in record["raw_detector_boxes"]
                    ]
                    selected, _ = allowed_boxes(raw_boxes, record["dataset"])
                    raw_score = score_boxes(gt_for(row_by_id[record["sample_id"]]), selected)
                    count.update(
                        {key: int(raw_score["per_class"][label][key]) for key in ("tp", "fp", "fn")}
                    )
                raw_per_class[label] = rates(dict(count))
                raw_total.update(count)
            source_result["raw_detector"] = {
                "micro": rates(dict(raw_total)),
                "per_class": raw_per_class,
            }
        result["sources"][source] = source_result
    return result


def render_gallery(
    output: Path, records: list[dict[str, Any]], rows: list[dict[str, str]], name: str
) -> None:
    gallery = output / "gallery"
    gallery.mkdir(exist_ok=True)
    row_ids = {row["id"]: row for row in rows}
    links = []
    for record in records:
        row = row_ids[record["sample_id"]]
        folder = gallery / record["sample_id"]
        folder.mkdir(exist_ok=True)
        source = DATA / row["image"]
        shutil.copy2(source, folder / "original.jpg")
        gt = [LabeledBox.model_validate(item) for item in record["ground_truth"]]
        pred = [LabeledBox.model_validate(item) for item in record["predictions"]]
        draw_boxes(source, gt, folder / "ground_truth.jpg", "ground truth")
        draw_boxes(source, pred, folder / "predictions.jpg", f"{name}: VLM result")
        if name == "yolo26m_guided":
            raw = [LabeledBox.model_validate(item) for item in record["raw_detector_boxes"]]
            draw_boxes(source, raw, folder / "yolo_raw.jpg", "YOLO26m raw conf>=0.25")
        write_json(folder / "result.json", record)
        micro = record["score"]["micro"]
        details = (
            f"<p>source={html.escape(record['dataset'])}; TP={micro['tp']} "
            f"FP={micro['fp']} FN={micro['fn']}; "
            f"error={html.escape(str(record['review_error']))}</p>"
        )
        images = ["original", "ground_truth"]
        if name == "yolo26m_guided":
            images.append("yolo_raw")
        images.append("predictions")
        folder.joinpath("index.html").write_text(
            "<meta charset='utf-8'><style>img{width:48%;vertical-align:top}</style>"
            f"<h2>{html.escape(record['sample_id'])}</h2>{details}"
            + "".join(f"<img src='{image}.jpg' title='{image}'>" for image in images)
            + "<p><a href='result.json'>structured result</a></p>",
            encoding="utf-8",
        )
        links.append(
            f"<tr><td><a href='{record['sample_id']}/index.html'>{record['sample_id']}</a></td>"
            f"<td>{record['dataset']}</td><td>{micro['tp']}</td><td>{micro['fp']}</td>"
            f"<td>{micro['fn']}</td></tr>"
        )
    gallery.joinpath("index.html").write_text(
        f"<meta charset='utf-8'><h1>{html.escape(name)} ({len(records)} images)</h1>"
        "<p>VLM API structured result; SEDD scores only PL/BLOCK.</p>"
        "<table><tr><th>sample</th><th>source</th><th>TP</th><th>FP</th><th>FN</th></tr>"
        + "".join(links)
        + "</table>",
        encoding="utf-8",
    )


async def main_async(groups: list[str], *, retry_errors: bool = False) -> None:
    rows = build_rows()
    OUT.mkdir(parents=True, exist_ok=True)
    write_json(
        OUT / "protocol.json",
        {
            "test_images": len(rows),
            "sewer2403": 562,
            "sedd": 44,
            "new_checkpoint": str(NEW),
            "groups": groups,
            "retry_errors": retry_errors,
            "fusion_policy": FUSION_VERSION,
            "vlm_model": Settings().vlm_model,
            "no_llm": True,
            "no_online_deployment": True,
        },
    )
    for group in groups:
        await run_group(
            group,
            rows,
            guided=group == "yolo26m_guided",
            retry_errors=retry_errors,
        )
    comparison = {}
    for group in groups:
        comparison[group] = json.loads((OUT / group / "summary.json").read_text(encoding="utf-8"))
    write_json(OUT / "comparison.json", comparison)
    print("COMPARISON COMPLETED", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", choices=["vlm_only", "yolo26m_guided", "all"], default="all")
    parser.add_argument(
        "--retry-errors",
        action="store_true",
        help="retry only saved samples with a prior review_error",
    )
    args = parser.parse_args()
    groups = ["vlm_only", "yolo26m_guided"] if args.group == "all" else [args.group]
    asyncio.run(main_async(groups, retry_errors=args.retry_errors))


if __name__ == "__main__":
    main()
