"""Reproducible local scale diagnostic for the fixed Sewer2403 YOLO weight.

This script never trains, calls a VLM/LLM, changes product policy, or writes into
the source datasets. Raw split and inference artifacts are created exclusively;
derived summaries and the small gallery may be regenerated.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import random
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "var/evaluations/2026-09-16-scale-followup"
SOURCE = ROOT / "var/datasets/sewer2403_source/extracted/市政下水雨污管道缺陷数据2403"
CACHED = ROOT / "var/evaluations/2026-09-16-sedd-yolo"
WEIGHT = ROOT / "yolov26/experiments/sewer2403_5class_yolo26s_img960_e100/weights/best.pt"
LABELS = ("PL", "BX", "FS", "BLOCK", "AJ")
AREA_BINS = ("<1%", "1-5%", "5-20%", ">=20%")
SEED = 20260916
CONFIDENCE = 0.25
IOU_THRESHOLD = 0.5
TILE_SIZE = 512
TILE_OVERLAP = 128
NMS_IOU = 0.5


def write_json(path: Path, payload: Any, *, derived: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w" if derived else "x", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def load_prepare_module():
    module_path = ROOT / "yolov26/code/prepare_sewer2403_yolo.py"
    spec = importlib.util.spec_from_file_location("scale_diagnostic_sewer_prepare", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load data-preparation module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _axis_starts(length: int, tile_size: int, overlap: int) -> list[int]:
    if length <= tile_size:
        return [0]
    stride = tile_size - overlap
    starts = list(range(0, length - tile_size + 1, stride))
    edge = length - tile_size
    if starts[-1] != edge:
        starts.append(edge)
    return starts


def tile_windows(
    width: int,
    height: int,
    *,
    tile_size: int = TILE_SIZE,
    overlap: int = TILE_OVERLAP,
) -> list[tuple[int, int, int, int]]:
    if width <= 0 or height <= 0 or tile_size <= 0:
        raise ValueError("image and tile dimensions must be positive")
    if overlap < 0 or overlap >= tile_size:
        raise ValueError("overlap must be nonnegative and smaller than tile_size")
    return [
        (x, y, min(x + tile_size, width), min(y + tile_size, height))
        for y in _axis_starts(height, tile_size, overlap)
        for x in _axis_starts(width, tile_size, overlap)
    ]


def map_tile_predictions(
    predictions: list[dict[str, Any]],
    *,
    tile: tuple[int, int, int, int],
    image_width: int,
    image_height: int,
) -> list[dict[str, Any]]:
    left, top, right, bottom = tile
    tile_width, tile_height = right - left, bottom - top
    mapped: list[dict[str, Any]] = []
    for prediction in predictions:
        x1, y1, x2, y2 = (float(value) for value in prediction["bbox_xyxy"])
        x1 = min(max(x1, 0.0), float(tile_width)) + left
        y1 = min(max(y1, 0.0), float(tile_height)) + top
        x2 = min(max(x2, 0.0), float(tile_width)) + left
        y2 = min(max(y2, 0.0), float(tile_height)) + top
        clipped = [
            min(max(float(x1), 0.0), float(image_width)),
            min(max(float(y1), 0.0), float(image_height)),
            min(max(float(x2), 0.0), float(image_width)),
            min(max(float(y2), 0.0), float(image_height)),
        ]
        if clipped[2] <= clipped[0] or clipped[3] <= clipped[1]:
            continue
        mapped.append(
            {
                "label": prediction["label"],
                "score": float(prediction["score"]),
                "bbox_xyxy": clipped,
            }
        )
    return mapped


def box_iou(left: list[float] | tuple[float, ...], right: list[float] | tuple[float, ...]) -> float:
    x1 = max(float(left[0]), float(right[0]))
    y1 = max(float(left[1]), float(right[1]))
    x2 = min(float(left[2]), float(right[2]))
    y2 = min(float(left[3]), float(right[3]))
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    if intersection == 0:
        return 0.0
    left_area = (float(left[2]) - float(left[0])) * (float(left[3]) - float(left[1]))
    right_area = (float(right[2]) - float(right[0])) * (float(right[3]) - float(right[1]))
    return intersection / (left_area + right_area - intersection)


def class_aware_nms(
    predictions: list[dict[str, Any]], *, iou_threshold: float = NMS_IOU
) -> list[dict[str, Any]]:
    if not 0 <= iou_threshold <= 1:
        raise ValueError("iou_threshold must be between zero and one")
    ordered = sorted(enumerate(predictions), key=lambda item: (-item[1]["score"], item[0]))
    kept: list[dict[str, Any]] = []
    for _, prediction in ordered:
        if any(
            existing["label"] == prediction["label"]
            and box_iou(existing["bbox_xyxy"], prediction["bbox_xyxy"]) >= iou_threshold
            for existing in kept
        ):
            continue
        kept.append(prediction)
    return kept


def match_detections(
    targets: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
    *,
    iou_threshold: float = IOU_THRESHOLD,
) -> list[tuple[int, int]]:
    matched_targets: set[int] = set()
    pairs: list[tuple[int, int]] = []
    ordered = sorted(enumerate(predictions), key=lambda item: (-item[1]["score"], item[0]))
    for prediction_index, prediction in ordered:
        candidates = [
            (box_iou(target["bbox_xyxy"], prediction["bbox_xyxy"]), target_index)
            for target_index, target in enumerate(targets)
            if target_index not in matched_targets and target["label"] == prediction["label"]
        ]
        if not candidates:
            continue
        overlap, target_index = max(candidates, key=lambda item: (item[0], -item[1]))
        if overlap >= iou_threshold:
            matched_targets.add(target_index)
            pairs.append((prediction_index, target_index))
    return pairs


def rates(tp: int, fp: int, fn: int) -> dict[str, int | float]:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1}


def relative_area(item: dict[str, Any], width: int, height: int) -> float:
    x1, y1, x2, y2 = (float(value) for value in item["bbox_xyxy"])
    return max(0.0, x2 - x1) * max(0.0, y2 - y1) / (width * height)


def area_bin(area: float) -> str:
    if area < 0.01:
        return "<1%"
    if area < 0.05:
        return "1-5%"
    if area < 0.20:
        return "5-20%"
    return ">=20%"


def score_records(
    targets: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
    width: int,
    height: int,
) -> dict[str, Any]:
    pairs = match_detections(targets, predictions)
    matched_predictions = {prediction for prediction, _ in pairs}
    matched_targets = {target for _, target in pairs}
    bin_counts = {name: Counter(tp=0, fp=0, fn=0) for name in AREA_BINS}
    for prediction_index, target_index in pairs:
        del prediction_index
        bin_counts[area_bin(relative_area(targets[target_index], width, height))]["tp"] += 1
    for target_index, target in enumerate(targets):
        if target_index not in matched_targets:
            bin_counts[area_bin(relative_area(target, width, height))]["fn"] += 1
    for prediction_index, prediction in enumerate(predictions):
        if prediction_index not in matched_predictions:
            bin_counts[area_bin(relative_area(prediction, width, height))]["fp"] += 1
    tp = len(pairs)
    return {
        "overall": rates(tp, len(predictions) - tp, len(targets) - tp),
        "area_bins": {name: rates(**bin_counts[name]) for name in AREA_BINS},
    }


def _empty_counts() -> dict[str, Counter[str]]:
    return {
        "overall": Counter(tp=0, fp=0, fn=0),
        **{name: Counter(tp=0, fp=0, fn=0) for name in AREA_BINS},
    }


def aggregate_scores(
    rows: list[dict[str, Any]], prediction_key: str, scope: tuple[str, ...]
) -> dict[str, Any]:
    counts = _empty_counts()
    per_class = {label: Counter(tp=0, fp=0, fn=0) for label in scope}
    target_areas: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        targets = [item for item in row["ground_truth"] if item["label"] in scope]
        predictions = [
            item
            for item in row[prediction_key]
            if item["label"] in scope and item.get("score", 1.0) >= CONFIDENCE
        ]
        result = score_records(targets, predictions, row["width"], row["height"])
        for key in ("tp", "fp", "fn"):
            counts["overall"][key] += int(result["overall"][key])
            for name in AREA_BINS:
                counts[name][key] += int(result["area_bins"][name][key])
        for label in scope:
            label_result = score_records(
                [item for item in targets if item["label"] == label],
                [item for item in predictions if item["label"] == label],
                row["width"],
                row["height"],
            )["overall"]
            for key in ("tp", "fp", "fn"):
                per_class[label][key] += int(label_result[key])
        for target in targets:
            target_areas[target["label"]].append(relative_area(target, row["width"], row["height"]))
    return {
        "images": len(rows),
        "scored_labels": list(scope),
        "overall": rates(**counts["overall"]),
        "area_bins": {name: rates(**counts[name]) for name in AREA_BINS},
        "per_class": {label: rates(**per_class[label]) for label in scope},
        "target_relative_area": {
            label: {
                "targets": len(areas),
                "median_percent": 100 * statistics.median(areas),
            }
            for label, areas in sorted(target_areas.items())
        },
    }


def legacy_group_key(image_path: Path, mode: str, prepare: Any) -> str:
    """Exact pre-fix grouping used to reconstruct, not recover, the missing split."""
    if mode == "none":
        return image_path.stem.lower()
    stem = prepare.normalized_source_stem(image_path)
    augmented_match = prepare.AUGMENTED_SET_RE.match(stem)
    if augmented_match:
        split_origin, source_id = augmented_match.groups()
        return f"augmentation:{split_origin.lower()}:{int(source_id)}"
    if mode == "video":
        old_patterns = (
            re.compile(r"^(.*?-(?:mp4|mpg|avi|mov))-t-\d", re.IGNORECASE),
            re.compile(r"^(.*?-(?:mp4|mpg|avi|mov))[_-]\d{6}[-_]", re.IGNORECASE),
        )
        for pattern in old_patterns:
            match = pattern.match(stem)
            if match:
                return f"video:{match.group(1).lower()}"
    return f"source:{stem.lower()}"


def groups_with_key(samples: list[Any], key_function: Any, prepare: Any) -> list[Any]:
    grouped: dict[str, list[Any]] = defaultdict(list)
    for sample in samples:
        grouped[key_function(sample.image_path)].append(sample)
    groups = []
    for key, group_samples in grouped.items():
        class_counts: Counter[int] = Counter()
        for sample in group_samples:
            class_counts.update(sample.source_class_ids)
        groups.append(
            prepare.SampleGroup(
                key=key,
                samples=sorted(group_samples, key=lambda sample: sample.image_path.name),
                class_image_counts=class_counts,
            )
        )
    return groups


def assignment_rows(assigned: dict[str, list[Any]], prepare: Any) -> list[dict[str, Any]]:
    rows = []
    for split in prepare.SPLIT_NAMES:
        for group in sorted(assigned[split], key=lambda item: item.key):
            for sample in group.samples:
                rows.append(
                    {
                        "split": split,
                        "image": sample.image_path.name,
                        "group_key": group.key,
                        "classes": [prepare.CLASSES[index] for index in sorted(sample.class_ids)],
                        "background_for_five_class_profile": not sample.labels,
                    }
                )
    return rows


def split_counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        split: {
            "images": sum(row["split"] == split for row in rows),
            "background_for_five_class_profile": sum(
                row["split"] == split and row["background_for_five_class_profile"] for row in rows
            ),
        }
        for split in ("train", "val", "test")
    }


def crossing_families(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["group_key"]].append(row)
    return [
        {
            "group_key": key,
            "images": sorted(row["image"] for row in group_rows),
            "splits": sorted({row["split"] for row in group_rows}),
        }
        for key, group_rows in sorted(grouped.items())
        if len({row["split"] for row in group_rows}) > 1
    ]


def prepare_splits() -> None:
    prepare = load_prepare_module()
    prepare.activate_profile("five")
    samples = prepare.load_samples(SOURCE)
    legacy_groups = groups_with_key(
        samples, lambda path: legacy_group_key(path, "video", prepare), prepare
    )
    corrected_groups = prepare.build_groups(samples, "video")
    split_args = dict(
        total_samples=len(samples),
        train_ratio=0.8,
        val_ratio=0.1,
        seed=3407,
        attempts=64,
        min_eval_class_ratio=0.08,
    )
    legacy_assigned, _, _ = prepare.stratified_group_split(groups=legacy_groups, **split_args)
    corrected_assigned, _, _ = prepare.stratified_group_split(groups=corrected_groups, **split_args)
    legacy_rows = assignment_rows(legacy_assigned, prepare)
    corrected_rows = assignment_rows(corrected_assigned, prepare)
    expected_legacy = {
        "train": {"images": 15239, "background_for_five_class_profile": 4938},
        "val": {"images": 1904, "background_for_five_class_profile": 695},
        "test": {"images": 1906, "background_for_five_class_profile": 696},
    }
    actual_legacy = split_counts(legacy_rows)
    if actual_legacy != expected_legacy:
        raise RuntimeError(
            f"legacy reconstruction no longer matches recorded counts: {actual_legacy!r}"
        )

    old_split = {row["image"]: row["split"] for row in legacy_rows}
    explicit_families: dict[str, list[str]] = defaultdict(list)
    explicit_re = re.compile(r"[_-]frame\d+$", re.IGNORECASE)
    for sample in samples:
        stem = prepare.normalized_source_stem(sample.image_path)
        if explicit_re.search(stem):
            explicit_families[prepare.group_key(sample.image_path, "video")].append(
                sample.image_path.name
            )
    crossed = [
        {
            "group_key": key,
            "images": sorted(images),
            "legacy_splits": sorted({old_split[image] for image in images}),
        }
        for key, images in sorted(explicit_families.items())
        if len({old_split[image] for image in images}) > 1
    ]
    explicit_keys = set(explicit_families)
    corrected_crossed = crossing_families(
        [row for row in corrected_rows if row["group_key"] in explicit_keys]
    )

    common = {
        "source": str(SOURCE),
        "profile": "five",
        "seed": 3407,
        "ratios": {"train": 0.8, "val": 0.1, "test": 0.1},
        "split_attempts": 64,
        "min_eval_class_ratio": 0.08,
    }
    write_json(
        OUT / "legacy-reconstructed-manifest.json",
        {
            **common,
            "provenance": (
                "reconstructed_legacy_algorithm_from_current_source_"
                "not_recovered_historical_manifest"
            ),
            "independence_claim": (
                "none; this is reconstructed evidence and not a proven unseen split for the "
                "existing weight"
            ),
            "samples": legacy_rows,
        },
    )
    write_json(
        OUT / "corrected-future-manifest.json",
        {
            **common,
            "provenance": "corrected_explicit_frame_grouping_candidate_for_future_training_only",
            "independence_claim": (
                "none for the existing weight; do not report this reshuffle as an independent "
                "test"
            ),
            "samples": corrected_rows,
        },
    )
    write_json(
        OUT / "split-summary.json",
        {
            "legacy_reconstructed": split_counts(legacy_rows),
            "corrected_future": split_counts(corrected_rows),
            "explicit_frame_families": len(explicit_families),
            "explicit_frame_families_crossing_reconstructed_legacy_splits": len(crossed),
            "crossing_families": crossed,
            "explicit_frame_families_crossing_corrected_future_splits": len(
                corrected_crossed
            ),
            "corrected_crossing_families": corrected_crossed,
            "warning": "Neither manifest proves unseen evaluation for the existing best.pt weight.",
        },
        derived=True,
    )
    print(json.dumps(json.loads((OUT / "split-summary.json").read_text()), ensure_ascii=False))


def summarize_splits() -> None:
    legacy = json.loads((OUT / "legacy-reconstructed-manifest.json").read_text(encoding="utf-8"))
    corrected = json.loads((OUT / "corrected-future-manifest.json").read_text(encoding="utf-8"))
    explicit_re = re.compile(r"[_-]frame\d+(?:_(?:jpg|jpeg|png|bmp))?\.rf\.", re.IGNORECASE)
    corrected_explicit = [row for row in corrected["samples"] if explicit_re.search(row["image"])]
    explicit_families: dict[str, list[str]] = defaultdict(list)
    for row in corrected_explicit:
        explicit_families[row["group_key"]].append(row["image"])
    legacy_split = {row["image"]: row["split"] for row in legacy["samples"]}
    legacy_crossed = [
        {
            "group_key": key,
            "images": sorted(images),
            "legacy_splits": sorted({legacy_split[image] for image in images}),
        }
        for key, images in sorted(explicit_families.items())
        if len({legacy_split[image] for image in images}) > 1
    ]
    corrected_crossed = crossing_families(corrected_explicit)
    summary_path = OUT / "split-summary.json"
    summary = {
        "legacy_reconstructed": split_counts(legacy["samples"]),
        "corrected_future": split_counts(corrected["samples"]),
        "explicit_frame_families": len(explicit_families),
        "explicit_frame_families_crossing_reconstructed_legacy_splits": len(
            legacy_crossed
        ),
        "crossing_families": legacy_crossed,
        "explicit_frame_families_crossing_corrected_future_splits": len(
            corrected_crossed
        ),
        "corrected_crossing_families": corrected_crossed,
        "warning": "Neither manifest proves unseen evaluation for the existing best.pt weight.",
    }
    write_json(summary_path, summary, derived=True)
    print(json.dumps(summary, ensure_ascii=False))


def cached_rows() -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in (CACHED / "predictions.jsonl").read_text(encoding="utf-8").splitlines()
    ]


def summarize_cached() -> None:
    rows = cached_rows()
    source_rows = [row for row in rows if row["dataset"] == "sewer2403"]
    summary = {
        "configuration": {
            "confidence": CONFIDENCE,
            "match_iou": IOU_THRESHOLD,
            "area_bins_by_original_image_fraction": list(AREA_BINS),
            "matched_tp_bin": "ground-truth relative area",
            "unmatched_fp_bin": "prediction relative area",
        },
        "sedd_1638": aggregate_scores(
            [row for row in rows if row["dataset"] == "sedd"], "predictions", ("PL", "BLOCK")
        ),
        "sewer2403_source_240": aggregate_scores(
            source_rows, "predictions", LABELS
        ),
        "sewer2403_source_240_pl_block_common_scope": aggregate_scores(
            source_rows, "predictions", ("PL", "BLOCK")
        ),
        "limitations": [
            "SEDD scores only PL/BLOCK because FS/BX/AJ are not annotated.",
            "The Sewer2403 source sample may overlap training and is not held-out accuracy.",
            "Relative-area bins describe target scale, not causal attribution of domain shift.",
        ],
    }
    write_json(OUT / "cached-area-summary.json", summary, derived=True)
    print(json.dumps(summary, ensure_ascii=False))


def prepare_ablation() -> None:
    manifest = json.loads((CACHED / "manifest.json").read_text(encoding="utf-8"))
    prepare = load_prepare_module()
    rng = random.Random(SEED)
    sedd = sorted(
        (row for row in manifest["samples"] if row["dataset"] == "sedd"),
        key=lambda row: row["sample_id"],
    )
    rng.shuffle(sedd)
    selected = sedd[:80]

    used_groups: set[str] = set()
    source_selected: list[dict[str, Any]] = []
    for offset, label in enumerate(LABELS):
        candidates = sorted(
            (
                dict(
                    row,
                    corrected_group_id=prepare.group_key(Path(row["image_path"]), "video"),
                )
                for row in manifest["samples"]
                if row["dataset"] == "sewer2403" and row.get("stratum") == label
            ),
            key=lambda row: row["sample_id"],
        )
        random.Random(SEED + offset + 1).shuffle(candidates)
        label_rows = []
        for row in candidates:
            if row["corrected_group_id"] in used_groups:
                continue
            used_groups.add(row["corrected_group_id"])
            label_rows.append(row)
            if len(label_rows) == 8:
                break
        if len(label_rows) != 8:
            raise RuntimeError(f"cannot select eight corrected groups for {label}")
        source_selected.extend(label_rows)
    selected.extend(source_selected)
    payload = {
        "seed": SEED,
        "selection": (
            "outcome-blind deterministic sampling from cached manifest before reading predictions"
        ),
        "counts": {"sedd": 80, "sewer2403_source": 40},
        "source_strata": {label: 8 for label in LABELS},
        "configuration": {
            "weight": str(WEIGHT),
            "imgsz": 960,
            "confidence": CONFIDENCE,
            "tile_size_original_pixels": TILE_SIZE,
            "tile_overlap_original_pixels": TILE_OVERLAP,
            "merge_nms_iou": NMS_IOU,
            "match_iou": IOU_THRESHOLD,
        },
        "limitations": [
            "SEDD source/video grouping is unknown.",
            "The Sewer2403 sample may overlap training and is not independent test evidence.",
            "This is a bounded paired diagnostic, not a hyperparameter search.",
        ],
        "samples": selected,
    }
    write_json(OUT / "ablation-manifest.json", payload)
    print(json.dumps(payload["counts"]))


def _plain_detections(batch: Any) -> list[dict[str, Any]]:
    return [
        {
            "label": detection.label.value,
            "score": float(detection.score),
            "bbox_xyxy": [float(value) for value in detection.bbox_xyxy],
        }
        for detection in batch.detections
    ]


def run_ablation() -> None:
    manifest = json.loads((OUT / "ablation-manifest.json").read_text(encoding="utf-8"))
    cached = {row["sample_id"]: row for row in cached_rows()}
    result_path = OUT / "ablation-predictions.jsonl"
    if result_path.exists():
        raise FileExistsError(f"refusing to overwrite raw predictions: {result_path}")
    sys.path.insert(0, str(ROOT / "backend/src"))
    from pipe_inspector.vision.detector import UltralyticsDetector

    detector = UltralyticsDetector(WEIGHT, device="0", imgsz=960, conf=CONFIDENCE)
    detector.load()
    write_json(
        OUT / "ablation-runtime.json",
        {
            **manifest["configuration"],
            "class_names": detector.class_names,
            "model_version": detector.model_version,
            "original_arm": (
                "cached full-image predictions from 2026-09-16-sedd-yolo, filtered at 0.25"
            ),
            "tiled_arm": "same cached full-image predictions plus new overlapping-tile predictions",
            "local_gpu_only": True,
            "llm_calls": 0,
            "vlm_calls": 0,
        },
    )
    started = time.perf_counter()
    with result_path.open("x", encoding="utf-8") as handle:
        for index, sample in enumerate(manifest["samples"]):
            source = cached[sample["sample_id"]]
            original = [
                {
                    "label": item["label"],
                    "score": float(item["score"]),
                    "bbox_xyxy": [float(value) for value in item["bbox_xyxy"]],
                }
                for item in source["predictions"]
                if item["score"] >= CONFIDENCE
            ]
            with Image.open(sample["image_path"]) as image_file:
                image = image_file.convert("RGB")
            windows = tile_windows(image.width, image.height)
            crops = [image.crop(window) for window in windows]
            tile_started = time.perf_counter()
            raw_results = detector._ensure_model().predict(
                source=crops,
                device=detector.device,
                imgsz=detector.imgsz,
                conf=detector.conf,
                verbose=False,
                batch=8,
            )
            mapped: list[dict[str, Any]] = []
            for window, raw_result in zip(windows, raw_results, strict=True):
                mapped.extend(
                    map_tile_predictions(
                        _plain_detections(detector._translate_result(raw_result)),
                        tile=window,
                        image_width=image.width,
                        image_height=image.height,
                    )
                )
            merged = class_aware_nms(original + mapped)
            record = {
                "sample_id": sample["sample_id"],
                "dataset": sample["dataset"],
                "image_path": sample["image_path"],
                "width": sample["width"],
                "height": sample["height"],
                "ground_truth": sample["ground_truth"],
                "tile_windows": windows,
                "original_predictions": original,
                "mapped_tile_predictions_before_merge": mapped,
                "full_plus_tiles_predictions": merged,
                "cached_original_elapsed_seconds": source.get("elapsed_seconds"),
                "tile_elapsed_seconds": time.perf_counter() - tile_started,
            }
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            if (index + 1) % 10 == 0:
                print(f"{index + 1}/{len(manifest['samples'])}", flush=True)
    detector.unload()
    write_json(
        OUT / "ablation-timing.json",
        {"images": len(manifest["samples"]), "wall_seconds": time.perf_counter() - started},
    )


def _draw_panel(
    image: Image.Image, items: list[dict[str, Any]], title: str, color: tuple[int, int, int]
) -> Image.Image:
    scale = min(640 / image.width, 420 / image.height, 1.0)
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    panel_image = image.resize(size)
    panel = Image.new("RGB", (size[0], size[1] + 28), (20, 23, 29))
    panel.paste(panel_image, (0, 28))
    draw = ImageDraw.Draw(panel)
    draw.text((6, 7), title, fill=color)
    for item in items:
        x1, y1, x2, y2 = (round(float(value) * scale) for value in item["bbox_xyxy"])
        draw.rectangle((x1, y1 + 28, x2, y2 + 28), outline=color, width=3)
        suffix = f" {item['score']:.2f}" if "score" in item else " GT"
        draw.text((x1 + 2, max(29, y1 + 29)), item["label"] + suffix, fill=color)
    return panel


def distinct_gallery_choices(
    rankings: list[tuple[str, list[dict[str, Any]]]],
    candidates: list[dict[str, Any]],
    *,
    limit: int,
) -> list[tuple[dict[str, Any], str]]:
    choices: list[tuple[dict[str, Any], str]] = []
    used: set[str] = set()
    for criterion, ranked in rankings:
        for row in ranked:
            if row["sample_id"] not in used:
                choices.append((row, criterion))
                used.add(row["sample_id"])
                break
    for row in sorted(candidates, key=lambda item: item["sample_id"]):
        if len(choices) >= limit:
            break
        if row["sample_id"] not in used:
            choices.append((row, "fixed-ID context case"))
            used.add(row["sample_id"])
    return choices


def build_gallery(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[tuple[dict[str, Any], str]] = []
    for dataset in ("sedd", "sewer2403"):
        candidates = [row for row in records if row["dataset"] == dataset]
        scope = ("PL", "BLOCK") if dataset == "sedd" else LABELS

        def deltas(row: dict[str, Any]) -> tuple[int, int]:
            targets = [item for item in row["ground_truth"] if item["label"] in scope]
            original = score_records(
                targets,
                [item for item in row["original_predictions"] if item["label"] in scope],
                row["width"],
                row["height"],
            )["overall"]
            tiled = score_records(
                targets,
                [item for item in row["full_plus_tiles_predictions"] if item["label"] in scope],
                row["width"],
                row["height"],
            )["overall"]
            return int(tiled["tp"] - original["tp"]), int(tiled["fp"] - original["fp"])

        ranked_gain = sorted(candidates, key=lambda row: (-deltas(row)[0], row["sample_id"]))
        ranked_fp = sorted(candidates, key=lambda row: (-deltas(row)[1], row["sample_id"]))
        choices = distinct_gallery_choices(
            [("ranked TP delta", ranked_gain), ("ranked FP delta", ranked_fp)],
            candidates,
            limit=3,
        )
        selected.extend(
            (
                row,
                (
                    f"{criterion} (TP {deltas(row)[0]:+d}, FP {deltas(row)[1]:+d})"
                    if criterion.startswith("ranked")
                    else criterion
                ),
            )
            for row, criterion in choices
        )

    gallery = OUT / "gallery"
    gallery.mkdir(parents=True, exist_ok=True)
    metadata = []
    links = []
    for row, criterion in selected:
        with Image.open(row["image_path"]) as image_file:
            image = image_file.convert("RGB")
        panels = [
            _draw_panel(image, row["ground_truth"], "Ground truth", (0, 255, 255)),
            _draw_panel(image, row["original_predictions"], "Full image", (255, 80, 80)),
            _draw_panel(
                image,
                row["full_plus_tiles_predictions"],
                "Full + overlap tiles",
                (255, 180, 0),
            ),
        ]
        canvas = Image.new(
            "RGB",
            (sum(panel.width for panel in panels), max(panel.height for panel in panels)),
            (20, 23, 29),
        )
        x = 0
        for panel in panels:
            canvas.paste(panel, (x, 0))
            x += panel.width
        filename = row["sample_id"] + ".png"
        canvas.save(gallery / filename)
        metadata.append({"sample_id": row["sample_id"], "selection_criterion": criterion})
        links.append(f'<li><p>{row["sample_id"]}: {criterion}</p><img src="{filename}"></li>')
    (gallery / "index.html").write_text(
        '<meta charset="utf-8"><title>Scale diagnostic gallery</title>'
        '<h1>GT / full image / full + overlapping tiles</h1>'
        '<p>Post-outcome diagnostic examples; selection criteria are stated and the cohort '
        'metrics, '
        'not these images, determine the result. No VLM/LLM was used.</p>'
        '<style>body{background:#14171d;color:#eee;font-family:sans-serif}img{max-width:100%}</style>'
        f"<ul>{''.join(links)}</ul>",
        encoding="utf-8",
    )
    write_json(gallery / "selection.json", metadata, derived=True)
    return metadata


def summarize_ablation() -> None:
    records = [
        json.loads(line)
        for line in (OUT / "ablation-predictions.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    summary: dict[str, Any] = {
        "configuration": {
            "confidence": CONFIDENCE,
            "match_iou": IOU_THRESHOLD,
            "area_bins_by_original_image_fraction": list(AREA_BINS),
            "tile_size": TILE_SIZE,
            "tile_overlap": TILE_OVERLAP,
            "merge_nms_iou": NMS_IOU,
        },
        "datasets": {},
        "limitations": [
            (
                "This fixed 120-image paired diagnostic is not a hyperparameter search or "
                "promotion study."
            ),
            "SEDD scores only PL/BLOCK and lacks known video/source grouping.",
            "The Sewer2403 sample may overlap training and is not held-out accuracy.",
            (
                "Tile time excludes the cached full-image inference time; wall time is recorded "
                "separately."
            ),
        ],
    }
    for dataset, scope in (("sedd", ("PL", "BLOCK")), ("sewer2403", LABELS)):
        subset = [row for row in records if row["dataset"] == dataset]
        summary["datasets"][dataset] = {
            "original": aggregate_scores(subset, "original_predictions", scope),
            "full_plus_tiles": aggregate_scores(subset, "full_plus_tiles_predictions", scope),
            "timing": {
                "cached_original_seconds": sum(
                    float(row["cached_original_elapsed_seconds"] or 0) for row in subset
                ),
                "new_tile_seconds": sum(float(row["tile_elapsed_seconds"]) for row in subset),
                "mean_new_tile_seconds_per_image": statistics.mean(
                    float(row["tile_elapsed_seconds"]) for row in subset
                ),
            },
        }
    summary["gallery_selection"] = build_gallery(records)
    write_json(OUT / "ablation-summary.json", summary, derived=True)
    print(json.dumps(summary, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode",
        choices=(
            "prepare-splits",
            "summarize-splits",
            "summarize-cached",
            "prepare-ablation",
            "run-ablation",
            "summarize-ablation",
        ),
    )
    mode = parser.parse_args().mode
    {
        "prepare-splits": prepare_splits,
        "summarize-splits": summarize_splits,
        "summarize-cached": summarize_cached,
        "prepare-ablation": prepare_ablation,
        "run-ablation": run_ablation,
        "summarize-ablation": summarize_ablation,
    }[mode]()


if __name__ == "__main__":
    main()
