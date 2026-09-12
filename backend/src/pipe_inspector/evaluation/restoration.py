"""Offline same-input restoration comparison with detector evidence and a local gallery."""

from __future__ import annotations

import argparse
import json
import math
import shutil
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from html import escape
from pathlib import Path
from typing import Any, Literal, Protocol

from PIL import Image, ImageDraw
from pydantic import Field, StrictInt, TypeAdapter, model_validator

from pipe_inspector.contracts.common import ContractModel, ImageRegion
from pipe_inspector.contracts.detection import DetectionBatch
from pipe_inspector.evaluation.review import LabeledBox, score_boxes

RESTORATION_KINDS = ("deblur", "dehaze", "super_resolution")
FULLFRAME_MAX_EDGE = 960
ROI_MAX_EDGE = 320
DETECTOR_IMGSZ = 960
DETECTOR_CONF = 0.25
MATCH_IOU = 0.5
STRONG_BASELINE_SCORE = 0.85


class _EnhancementAdapter(Protocol):
    kind: str
    model_id: str
    model_version: str
    scale: int

    def enhance_image(self, image: Image.Image) -> Image.Image: ...

    def unload(self) -> None: ...


class _CudaProbeProtocol(Protocol):
    def begin(self) -> None: ...

    def finish(self) -> dict[str, Any]: ...


class _CudaProbe:
    """Best-effort CUDA synchronization and peak allocation measurement."""

    def __init__(self, device: str) -> None:
        self._torch: Any | None = None
        self._device: Any | None = None
        self._status = "not_applicable"
        self._error: dict[str, str] | None = None
        self._active = False
        if not (device == "cuda" or device.startswith("cuda:")):
            return
        try:
            import torch

            if not torch.cuda.is_available():
                self._status = "unavailable"
                return
            self._torch = torch
            self._device = torch.device(device)
            self._status = "available"
        except Exception as error:
            self._status = "unavailable"
            self._error = _error(error)

    def begin(self) -> None:
        self._active = False
        if self._status != "available":
            return
        try:
            # Synchronize initializes CUDA before reset_peak_memory_stats. Resetting on an
            # uninitialized runtime can otherwise raise "invalid device argument".
            self._torch.cuda.synchronize(self._device)
            self._torch.cuda.reset_peak_memory_stats(self._device)
            self._active = True
        except Exception as error:
            self._status = "unavailable"
            self._error = _error(error)

    def finish(self) -> dict[str, Any]:
        peak: int | None = None
        synchronized = False
        if self._active:
            try:
                self._torch.cuda.synchronize(self._device)
                peak = int(self._torch.cuda.max_memory_allocated(self._device))
                synchronized = True
            except Exception as error:
                self._status = "unavailable"
                self._error = _error(error)
        result = {
            "status": self._status,
            "peak_allocated_bytes": peak,
            "synchronized": synchronized,
        }
        if self._error is not None:
            result["error"] = self._error
        self._active = False
        return result


class RestorationSample(ContractModel):
    sample_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    image: Path
    roi: tuple[StrictInt, StrictInt, StrictInt, StrictInt]
    ground_truth: tuple[LabeledBox, ...] = ()
    reason: str = Field(min_length=1)


class RestorationModel(ContractModel):
    model_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    kind: Literal["deblur", "dehaze", "super_resolution"]
    checkpoint: Path


class _Manifest(ContractModel):
    samples: tuple[RestorationSample, ...] = Field(min_length=1, max_length=12)
    models: tuple[RestorationModel, ...]

    @model_validator(mode="after")
    def validate_comparison_shape(self) -> "_Manifest":
        sample_ids = [sample.sample_id for sample in self.samples]
        if len(sample_ids) != len(set(sample_ids)):
            raise ValueError("sample_id values must be unique")
        model_ids = [model.model_id for model in self.models]
        if len(model_ids) != len(set(model_ids)):
            raise ValueError("model_id values must be unique")
        counts = Counter(model.kind for model in self.models)
        if counts != Counter({kind: 2 for kind in RESTORATION_KINDS}):
            raise ValueError(
                "models manifest requires exactly two models for each restoration kind"
            )
        return self


def _error(error: Exception) -> dict[str, str]:
    return {"type": type(error).__name__, "message": str(error)}


def _iou(left: Sequence[float], right: Sequence[float]) -> float:
    lx1, ly1, lx2, ly2 = left
    rx1, ry1, rx2, ry2 = right
    intersection_width = max(0.0, min(lx2, rx2) - max(lx1, rx1))
    intersection_height = max(0.0, min(ly2, ry2) - max(ly1, ry1))
    intersection = intersection_width * intersection_height
    if intersection == 0:
        return 0.0
    left_area = (lx2 - lx1) * (ly2 - ly1)
    right_area = (rx2 - rx1) * (ry2 - ry1)
    return intersection / (left_area + right_area - intersection)


def compare_box_sets(
    baseline: Sequence[LabeledBox],
    candidate: Sequence[LabeledBox],
    *,
    iou_threshold: float = MATCH_IOU,
    strong_score: float = STRONG_BASELINE_SCORE,
) -> dict[str, Any]:
    """Describe same-class one-to-one retention without assigning truth labels."""
    if not math.isfinite(iou_threshold) or not 0.0 <= iou_threshold <= 1.0:
        raise ValueError("iou_threshold must be finite and between zero and one")
    if not math.isfinite(strong_score) or not 0.0 <= strong_score <= 1.0:
        raise ValueError("strong_score must be finite and between zero and one")

    overlaps: dict[tuple[int, int], float] = {}
    adjacency: dict[int, list[int]] = {}
    for baseline_index, baseline_box in enumerate(baseline):
        eligible = []
        for candidate_index, candidate_box in enumerate(candidate):
            if baseline_box.label != candidate_box.label:
                continue
            overlap = _iou(baseline_box.bbox_xyxy, candidate_box.bbox_xyxy)
            if overlap >= iou_threshold:
                overlaps[(baseline_index, candidate_index)] = overlap
                eligible.append(candidate_index)
        adjacency[baseline_index] = sorted(
            eligible,
            key=lambda index: (-candidate[index].score, -overlaps[(baseline_index, index)], index),
        )

    candidate_owner: dict[int, int] = {}

    def augment(baseline_index: int, visited_candidates: set[int]) -> bool:
        for candidate_index in adjacency[baseline_index]:
            if candidate_index in visited_candidates:
                continue
            visited_candidates.add(candidate_index)
            previous_owner = candidate_owner.get(candidate_index)
            if previous_owner is None or augment(previous_owner, visited_candidates):
                candidate_owner[candidate_index] = baseline_index
                return True
        return False

    # Processing stronger baselines first makes maximum-cardinality ties retain them.
    baseline_priority = sorted(
        range(len(baseline)), key=lambda index: (-baseline[index].score, index)
    )
    for baseline_index in baseline_priority:
        augment(baseline_index, set())

    matches: list[dict[str, int | float]] = [
        {
            "baseline_index": baseline_index,
            "candidate_index": candidate_index,
            "iou": overlaps[(baseline_index, candidate_index)],
        }
        for candidate_index, baseline_index in sorted(candidate_owner.items())
    ]
    matched_baselines = set(candidate_owner.values())
    matched_candidates = set(candidate_owner)
    retained = sorted(matched_baselines)
    lost = [index for index in range(len(baseline)) if index not in matched_baselines]
    new = [index for index in range(len(candidate)) if index not in matched_candidates]
    strong = [index for index, box in enumerate(baseline) if box.score >= strong_score]
    strong_retained = [index for index in strong if index in matched_baselines]
    strong_lost = [index for index in strong if index not in matched_baselines]
    return {
        "iou_threshold": iou_threshold,
        "baseline_count": len(baseline),
        "candidate_count": len(candidate),
        "matches": sorted(matches, key=lambda item: int(item["candidate_index"])),
        "retained_baseline_indices": retained,
        "lost_baseline_indices": lost,
        "new_candidate_indices": new,
        "strong_baseline": {
            "threshold": strong_score,
            "indices": strong,
            "retained_indices": strong_retained,
            "lost_indices": strong_lost,
            "retention": len(strong_retained) / len(strong) if strong else None,
        },
    }


def _load_manifest(samples_path: Path, models_path: Path) -> _Manifest:
    samples = TypeAdapter(tuple[RestorationSample, ...]).validate_json(
        samples_path.read_text(encoding="utf-8")
    )
    models = TypeAdapter(tuple[RestorationModel, ...]).validate_json(
        models_path.read_text(encoding="utf-8")
    )
    return _Manifest(samples=samples, models=models)


def _resolve(path: Path, manifest_path: Path) -> Path:
    return path.resolve() if path.is_absolute() else (manifest_path.parent / path).resolve()


def _prepare_inputs(
    manifest: _Manifest, samples_path: Path, models_path: Path
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    samples: list[dict[str, Any]] = []
    for sample in manifest.samples:
        image_path = _resolve(sample.image, samples_path)
        if not image_path.is_file():
            raise ValueError(f"sample image does not exist: {image_path}")
        try:
            with Image.open(image_path) as image:
                image.load()
                width, height = image.size
        except OSError as error:
            raise ValueError(f"sample image is not readable: {image_path}") from error
        try:
            region = ImageRegion(width=width, height=height, bbox_xyxy=sample.roi)
        except ValueError as error:
            raise ValueError(f"invalid roi for sample {sample.sample_id}: {error}") from error
        x1, y1, x2, y2 = (int(value) for value in region.bbox_xyxy)
        if x2 - x1 > ROI_MAX_EDGE or y2 - y1 > ROI_MAX_EDGE:
            raise ValueError(f"roi must be at most {ROI_MAX_EDGE}x{ROI_MAX_EDGE} pixels")
        for target in sample.ground_truth:
            ImageRegion(width=width, height=height, bbox_xyxy=target.bbox_xyxy)
        samples.append(
            {
                "contract": sample,
                "image_path": image_path,
                "original_size": (width, height),
                "roi": (x1, y1, x2, y2),
            }
        )
    models = [
        {
            "contract": model,
            "checkpoint": _resolve(model.checkpoint, models_path),
        }
        for model in manifest.models
    ]
    return samples, models


def _fullframe_size(width: int, height: int) -> tuple[int, int]:
    scale = min(1.0, FULLFRAME_MAX_EDGE / max(width, height))
    return max(1, round(width * scale)), max(1, round(height * scale))


def _artifact(
    *,
    artifact_id: str,
    role: str,
    kind: str,
    image_path: str,
    image_size: tuple[int, int],
    offset: tuple[float, float],
    original_scale: tuple[float, float],
) -> dict[str, Any]:
    return {
        "artifact_id": artifact_id,
        "role": role,
        "kind": kind,
        "image": image_path,
        "image_size": list(image_size),
        "mapping_to_original": {
            "offset": list(offset),
            "scale": list(original_scale),
            "coordinate_space": "original_pixels",
        },
    }


def _save_controls(
    output: Path, prepared: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    sample: RestorationSample = prepared["contract"]
    source_path: Path = prepared["image_path"]
    original_width, original_height = prepared["original_size"]
    roi = prepared["roi"]
    sample_dir = output / sample.sample_id
    sample_dir.mkdir()
    suffix = source_path.suffix.lower() or ".image"
    original_relative = f"{sample.sample_id}/full-original{suffix}"
    shutil.copyfile(source_path, output / original_relative)

    with Image.open(source_path) as opened:
        source = opened.convert("RGB")
        fullframe_size = _fullframe_size(original_width, original_height)
        if fullframe_size == source.size:
            fullframe = source.copy()
        else:
            fullframe = source.resize(fullframe_size, Image.Resampling.LANCZOS)
        native_roi = source.crop(roi)
    fullframe_relative = f"{sample.sample_id}/control-fullframe.png"
    roi_relative = f"{sample.sample_id}/control-roi-native.png"
    bicubic_relative = f"{sample.sample_id}/control-roi-bicubic-x4.png"
    fullframe.save(output / fullframe_relative, format="PNG")
    native_roi.save(output / roi_relative, format="PNG")
    bicubic = native_roi.resize(
        (native_roi.width * 4, native_roi.height * 4), Image.Resampling.BICUBIC
    )
    bicubic.save(output / bicubic_relative, format="PNG")
    fullframe.close()
    native_roi.close()
    bicubic.close()

    x1, y1, x2, y2 = roi
    roi_width, roi_height = x2 - x1, y2 - y1
    controls = {
        "fullframe": _artifact(
            artifact_id="fullframe",
            role="control",
            kind="fullframe",
            image_path=fullframe_relative,
            image_size=fullframe_size,
            offset=(0.0, 0.0),
            original_scale=(
                original_width / fullframe_size[0],
                original_height / fullframe_size[1],
            ),
        ),
        "roi_native": _artifact(
            artifact_id="roi_native",
            role="control",
            kind="roi_native",
            image_path=roi_relative,
            image_size=(roi_width, roi_height),
            offset=(x1, y1),
            original_scale=(1.0, 1.0),
        ),
        "roi_bicubic_x4": _artifact(
            artifact_id="roi_bicubic_x4",
            role="control",
            kind="roi_bicubic_x4",
            image_path=bicubic_relative,
            image_size=(roi_width * 4, roi_height * 4),
            offset=(x1, y1),
            original_scale=(0.25, 0.25),
        ),
    }
    row = {
        "sample_id": sample.sample_id,
        "reason": sample.reason,
        "input_path": str(source_path),
        "full_original": original_relative,
        "analysis": {
            "original_size": [original_width, original_height],
            "fullframe_size": list(fullframe_size),
            "fullframe_to_original": [
                original_width / fullframe_size[0],
                original_height / fullframe_size[1],
            ],
            "roi_original_xyxy": list(roi),
            "roi_native_size": [roi_width, roi_height],
            "sr_crop_coordinate_space": "original_pixels",
        },
        "ground_truth": [target.model_dump(mode="json") for target in sample.ground_truth],
        "controls": controls,
        "models": {},
    }
    return row, controls


def _validate_adapter(adapter: _EnhancementAdapter, model: RestorationModel) -> None:
    expected_scale = 4 if model.kind == "super_resolution" else 1
    if adapter.model_id != model.model_id:
        raise ValueError(
            f"adapter model_id mismatch: expected {model.model_id}, got {adapter.model_id}"
        )
    if adapter.kind != model.kind:
        raise ValueError(f"adapter kind mismatch: expected {model.kind}, got {adapter.kind}")
    if not isinstance(adapter.model_version, str) or not adapter.model_version.strip():
        raise ValueError("adapter model_version must be nonempty")
    if type(adapter.scale) is not int or adapter.scale != expected_scale:
        raise ValueError(
            f"adapter scale mismatch for {model.kind}: expected {expected_scale}, "
            f"got {adapter.scale}"
        )


def _run_models(
    output: Path,
    samples: Sequence[Mapping[str, Any]],
    model_inputs: Sequence[Mapping[str, Any]],
    rows: Sequence[dict[str, Any]],
    *,
    device: str,
    model_factory: Callable[[str, Path, str], _EnhancementAdapter],
    cuda_probe: _CudaProbeProtocol,
) -> list[dict[str, Any]]:
    model_records: list[dict[str, Any]] = []
    for prepared_model in model_inputs:
        model: RestorationModel = prepared_model["contract"]
        checkpoint: Path = prepared_model["checkpoint"]
        model_record: dict[str, Any] = {
            "model_id": model.model_id,
            "kind": model.kind,
            "checkpoint": str(checkpoint),
            "invocation_failures": 0,
        }
        adapter: _EnhancementAdapter | None = None
        try:
            adapter = model_factory(model.model_id, checkpoint, device)
            _validate_adapter(adapter, model)
            model_record.update(
                model_version=adapter.model_version,
                scale=adapter.scale,
                status="completed",
            )
        except Exception as error:
            model_record.update(status="failed", load_error=_error(error))
            model_record["invocation_failures"] = len(samples)
            if adapter is not None:
                try:
                    adapter.unload()
                except Exception as unload_error:
                    model_record["unload_error"] = _error(unload_error)
            for row in rows:
                row["models"][model.model_id] = {
                    "status": "failed",
                    "kind": model.kind,
                    "checkpoint": str(checkpoint),
                    "duration_seconds": 0.0,
                    "error": _error(error),
                }
            model_records.append(model_record)
            continue

        try:
            for sample_index, (_prepared, row) in enumerate(zip(samples, rows, strict=True)):
                sample_id = row["sample_id"]
                control_name = "roi_native" if model.kind == "super_resolution" else "fullframe"
                control = row["controls"][control_name]
                input_path = output / control["image"]
                expected_width, expected_height = control["image_size"]
                expected_width *= adapter.scale
                expected_height *= adapter.scale
                relative = f"{sample_id}/model-{model.model_id}.png"
                cuda_probe.begin()
                started = time.perf_counter()
                enhanced: Image.Image | None = None
                model_input: Image.Image | None = None
                artifact: dict[str, Any] | None = None
                invocation_error: Exception | None = None
                try:
                    with Image.open(input_path) as opened:
                        model_input = opened.convert("RGB")
                    enhanced = adapter.enhance_image(model_input)
                    if not isinstance(enhanced, Image.Image):
                        raise ValueError("model output must be a PIL image")
                    if enhanced.mode != "RGB":
                        raise ValueError(f"model output must be RGB, got {enhanced.mode}")
                    if enhanced.size != (expected_width, expected_height):
                        raise ValueError(
                            "model output has wrong size: expected "
                            f"{expected_width}x{expected_height}, got "
                            f"{enhanced.width}x{enhanced.height}"
                        )
                    enhanced.save(output / relative, format="PNG")
                    scale_x, scale_y = control["mapping_to_original"]["scale"]
                    artifact = _artifact(
                        artifact_id=f"model-{model.model_id}",
                        role="model_output",
                        kind=model.kind,
                        image_path=relative,
                        image_size=(expected_width, expected_height),
                        offset=tuple(control["mapping_to_original"]["offset"]),
                        original_scale=(scale_x / adapter.scale, scale_y / adapter.scale),
                    )
                except Exception as error:
                    invocation_error = error
                finally:
                    if model_input is not None:
                        model_input.close()
                    if isinstance(enhanced, Image.Image):
                        enhanced.close()
                measurement = cuda_probe.finish()
                duration = time.perf_counter() - started
                common = {
                    "kind": model.kind,
                    "checkpoint": str(checkpoint),
                    "model_version": adapter.model_version,
                    "scale": adapter.scale,
                    "duration_seconds": duration,
                    "timing": {
                        "duration_seconds": duration,
                        "scope": (
                            "control_png_decode+adapter_enhance+output_validation+png_encode"
                        ),
                        "first_adapter_invocation": sample_index == 0,
                        "cuda_synchronized": measurement["synchronized"],
                    },
                    "cuda_peak_allocated_bytes": measurement["peak_allocated_bytes"],
                    "cuda_measurement_status": measurement["status"],
                }
                if "error" in measurement:
                    common["cuda_measurement_error"] = measurement["error"]
                if invocation_error is None:
                    row["models"][model.model_id] = {
                        "status": "succeeded",
                        **common,
                        "artifact": artifact,
                    }
                else:
                    model_record["invocation_failures"] += 1
                    model_record["status"] = "completed_with_errors"
                    row["models"][model.model_id] = {
                        "status": "failed",
                        **common,
                        "error": _error(invocation_error),
                    }
        finally:
            try:
                adapter.unload()
            except Exception as error:
                model_record["unload_error"] = _error(error)
                if model_record["status"] == "completed":
                    model_record["status"] = "completed_with_unload_error"
        model_records.append(model_record)
    return model_records


def _mapped_boxes(batch: DetectionBatch, artifact: Mapping[str, Any]) -> list[LabeledBox]:
    offset_x, offset_y = artifact["mapping_to_original"]["offset"]
    scale_x, scale_y = artifact["mapping_to_original"]["scale"]
    boxes = []
    for detection in batch.detections:
        x1, y1, x2, y2 = detection.bbox_xyxy
        boxes.append(
            LabeledBox(
                label=detection.label,
                score=detection.score,
                bbox_xyxy=(
                    offset_x + x1 * scale_x,
                    offset_y + y1 * scale_y,
                    offset_x + x2 * scale_x,
                    offset_y + y2 * scale_y,
                ),
            )
        )
    return boxes


def _draw_overlay(source: Path, destination: Path, batch: DetectionBatch) -> None:
    colours = {
        "AJ": "#22d3ee",
        "BX": "#fb7185",
        "FS": "#facc15",
        "PL": "#4ade80",
        "BLOCK": "#c084fc",
    }
    with Image.open(source) as opened:
        image = opened.convert("RGB")
    draw = ImageDraw.Draw(image)
    stroke = max(2, round(max(image.size) / 480))
    for index, detection in enumerate(batch.detections):
        coordinates = tuple(float(value) for value in detection.bbox_xyxy)
        label = detection.label.value
        colour = colours[label]
        draw.rectangle(coordinates, outline=colour, width=stroke)
        text = f"#{index} {label} {detection.score:.3f}"
        x1, y1, _, _ = coordinates
        text_y = max(0.0, y1 - 12)
        draw.text((x1, text_y), text, fill=colour, stroke_width=1, stroke_fill="#111827")
    image.save(destination, format="PNG")
    image.close()


def _all_artifacts(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    artifacts = list(row["controls"].values())
    artifacts.extend(
        job["artifact"] for job in row["models"].values() if job["status"] == "succeeded"
    )
    return artifacts


def _add_aligned_detail_crops(output: Path, rows: Sequence[dict[str, Any]]) -> None:
    for row in rows:
        roi_x1, roi_y1, roi_x2, roi_y2 = row["analysis"]["roi_original_xyxy"]
        fullframe = row["controls"]["fullframe"]
        offset_x, offset_y = fullframe["mapping_to_original"]["offset"]
        scale_x, scale_y = fullframe["mapping_to_original"]["scale"]
        width, height = fullframe["image_size"]
        crop_box = (
            max(0, math.floor((roi_x1 - offset_x) / scale_x)),
            max(0, math.floor((roi_y1 - offset_y) / scale_y)),
            min(width, math.ceil((roi_x2 - offset_x) / scale_x)),
            min(height, math.ceil((roi_y2 - offset_y) / scale_y)),
        )
        if crop_box[0] >= crop_box[2] or crop_box[1] >= crop_box[3]:
            raise ValueError(f"fixed ROI does not map into fullframe for {row['sample_id']}")
        artifacts = [fullframe]
        artifacts.extend(
            job["artifact"]
            for job in row["models"].values()
            if job["status"] == "succeeded" and job["kind"] in {"deblur", "dehaze"}
        )
        for artifact in artifacts:
            if artifact["image_size"] != fullframe["image_size"]:
                raise ValueError(
                    f"fullframe restoration size mismatch for {artifact['artifact_id']}"
                )
            source_path = output / artifact["image"]
            relative = f"{row['sample_id']}/{source_path.stem}-fixed-roi.png"
            with Image.open(source_path) as opened:
                crop = opened.convert("RGB").crop(crop_box)
            crop.save(output / relative, format="PNG")
            crop.close()
            artifact["detail_crop"] = {
                "image": relative,
                "image_size": [crop_box[2] - crop_box[0], crop_box[3] - crop_box[1]],
                "analysis_bbox_xyxy": list(crop_box),
                "source_roi_original_xyxy": [roi_x1, roi_y1, roi_x2, roi_y2],
            }


def _rebuild_comparisons(rows: Sequence[dict[str, Any]]) -> None:
    for row in rows:
        controls = row["controls"]
        comparisons = [(controls["roi_bicubic_x4"], "roi_native")]
        comparisons.extend(
            (
                job["artifact"],
                "roi_native" if job["kind"] == "super_resolution" else "fullframe",
            )
            for job in row["models"].values()
            if job["status"] == "succeeded"
        )
        for artifact, baseline_name in comparisons:
            artifact["comparison_baseline"] = baseline_name
            artifact.pop("baseline_comparison", None)
            baseline = controls[baseline_name]
            if (
                artifact.get("detector_status") == "succeeded"
                and baseline.get("detector_status") == "succeeded"
            ):
                baseline_boxes = [
                    LabeledBox.model_validate(item) for item in baseline["mapped_detections"]
                ]
                candidate_boxes = [
                    LabeledBox.model_validate(item) for item in artifact["mapped_detections"]
                ]
                artifact["baseline_comparison"] = compare_box_sets(
                    baseline_boxes, candidate_boxes
                )


def _write_artifact_json(output: Path, rows: Sequence[dict[str, Any]]) -> None:
    for row in rows:
        for artifact in _all_artifacts(row):
            payload = {"sample_id": row["sample_id"], "artifact": artifact}
            (output / artifact["detections_json"]).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )


def _detect_artifacts(
    output: Path,
    rows: Sequence[dict[str, Any]],
    *,
    detector_checkpoint: Path,
    device: str,
    detector_factory: Callable[..., Any],
) -> dict[str, Any]:
    detector_record: dict[str, Any] = {
        "checkpoint": str(detector_checkpoint.resolve()),
        "device": device,
        "imgsz": DETECTOR_IMGSZ,
        "conf": DETECTOR_CONF,
    }
    detector = None
    try:
        detector = detector_factory(
            checkpoint=detector_checkpoint,
            device=device,
            imgsz=DETECTOR_IMGSZ,
            conf=DETECTOR_CONF,
        )
    except Exception as error:
        detector_record.update(status="failed", load_error=_error(error))

    for row in rows:
        targets = tuple(LabeledBox.model_validate(item) for item in row["ground_truth"])
        for artifact in _all_artifacts(row):
            source = output / artifact["image"]
            stem = source.stem
            relative_json = f"{row['sample_id']}/{stem}-detections.json"
            artifact["detections_json"] = relative_json
            if detector is None:
                artifact["detector_status"] = "failed"
                artifact["detector_error"] = detector_record["load_error"]
                continue
            try:
                batch = DetectionBatch.model_validate(detector.predict_image(source))
                expected_size = tuple(artifact["image_size"])
                if (batch.width, batch.height) != expected_size:
                    raise ValueError(
                        "detector dimensions differ from artifact: expected "
                        f"{expected_size[0]}x{expected_size[1]}, got {batch.width}x{batch.height}"
                    )
                mapped = _mapped_boxes(batch, artifact)
                relative_overlay = f"{row['sample_id']}/{stem}-overlay.png"
                _draw_overlay(source, output / relative_overlay, batch)
                artifact.update(
                    detector_status="succeeded",
                    detector=batch.model_dump(mode="json"),
                    mapped_detections=[box.model_dump(mode="json") for box in mapped],
                    overlay=relative_overlay,
                )
                if artifact["kind"] in {"fullframe", "dehaze", "deblur"}:
                    artifact["ground_truth_score"] = score_boxes(targets, mapped)
            except Exception as error:
                artifact["detector_status"] = "failed"
                artifact["detector_error"] = _error(error)

    if detector is not None:
        detector_record["status"] = "completed"
        unload = getattr(detector, "unload", None)
        if callable(unload):
            try:
                unload()
            except Exception as error:
                detector_record["unload_error"] = _error(error)
                detector_record["status"] = "completed_with_unload_error"

    _rebuild_comparisons(rows)
    _write_artifact_json(output, rows)
    return detector_record


def _artifact_markup(
    artifact: Mapping[str, Any], title: str, *, note: str | None = None
) -> str:
    image = Path(str(artifact["image"])).name
    raw_json = Path(str(artifact["detections_json"])).name
    status = artifact.get("detector_status")
    if status == "succeeded":
        overlay = Path(str(artifact["overlay"])).name
        detector_note = (
            '<details><summary>显示检测叠图 PNG</summary>'
            f'<a href="{escape(overlay, quote=True)}">'
            f'<img src="{escape(overlay, quote=True)}" '
            f'alt="{escape(title, quote=True)} 检测叠图"></a></details>'
            f'<a href="{escape(raw_json, quote=True)}">检测与原图坐标 JSON</a>'
        )
    else:
        message = artifact.get("detector_error", {}).get("message", "detector unavailable")
        detector_note = (
            f'检测失败：{escape(message)} · '
            f'<a href="{escape(raw_json, quote=True)}">失败 JSON</a>'
        )
    note_markup = "" if note is None else f"<p>{escape(note)}</p>"
    crop_markup = ""
    if artifact.get("detail_crop") is not None:
        crop_image = Path(str(artifact["detail_crop"]["image"])).name
        crop_markup = (
            '<details open><summary>固定 ROI 对齐细节</summary>'
            f'<a href="{escape(crop_image, quote=True)}">'
            f'<img src="{escape(crop_image, quote=True)}" '
            f'alt="{escape(title, quote=True)} 固定 ROI 对齐细节"></a></details>'
        )
    return (
        f'<article><h3>{escape(title)}</h3><a href="{escape(image, quote=True)}">'
        f'<img src="{escape(image, quote=True)}" alt="{escape(title, quote=True)}"></a>'
        f"{note_markup}{crop_markup}<p>{detector_note}</p></article>"
    )


def _write_gallery(
    output: Path,
    rows: Sequence[Mapping[str, Any]],
    models: Sequence[Mapping[str, Any]],
    detector: Mapping[str, Any],
) -> None:
    index_sections = []
    for row in rows:
        sample_id = str(row["sample_id"])
        sample_dir = output / sample_id
        controls = row["controls"]

        def model_card(model_id: str, job: Mapping[str, Any]) -> str:
            if job["status"] == "succeeded":
                return _artifact_markup(
                    job["artifact"],
                    f"模型：{model_id}",
                    note=f'耗时 {job["duration_seconds"]:.3f} 秒',
                )
            error = job["error"]
            return (
                f'<article class="failure"><h3>模型：{escape(model_id)}</h3>'
                f'<p>失败耗时 {job["duration_seconds"]:.3f} 秒；'
                f'{escape(error["type"])}：{escape(error["message"])}</p></article>'
            )

        def cards_for(kind: str) -> list[str]:
            return [
                model_card(model_id, job)
                for model_id, job in row["models"].items()
                if job["kind"] == kind
            ]

        groups = [
            (
                "去模糊",
                [
                    _artifact_markup(controls["fullframe"], "全图分析控制"),
                    *cards_for("deblur"),
                ],
            ),
            (
                "去雾",
                [
                    _artifact_markup(controls["fullframe"], "全图分析控制"),
                    *cards_for("dehaze"),
                ],
            ),
            (
                "ROI 超分",
                [
                    _artifact_markup(controls["roi_native"], "原生 ROI 控制"),
                    _artifact_markup(controls["roi_bicubic_x4"], "Bicubic ×4 ROI 控制"),
                    *cards_for("super_resolution"),
                ],
            ),
        ]
        grouped_markup = [
            f'<section><h2>{escape(title)}</h2><div class="grid">'
            f'{"".join(cards)}</div></section>'
            for title, cards in groups
        ]
        original = Path(str(row["full_original"])).name
        detail = "\n".join(
            [
                '<!doctype html><html lang="zh-CN"><meta charset="utf-8">',
                '<meta name="viewport" content="width=device-width,initial-scale=1">',
                f"<title>{escape(sample_id)} · 恢复对照</title>",
                "<style>body{font:16px sans-serif;max-width:1400px;margin:2rem auto;padding:1rem}"
                ".grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:1rem}"
                "article{border:1px solid #aaa;padding:.7rem}"
                "img{width:100%;height:260px;object-fit:contain}"
                ".failure{border-color:#b91c1c}</style>",
                f"<h1>{escape(sample_id)}</h1><p>{escape(str(row['reason']))}</p>",
                f'<p><a href="{escape(original, quote=True)}">未改字节的完整原图</a> · '
                '<a href="../results.json">完整结果 JSON</a></p>',
                *grouped_markup,
                "</html>",
            ]
        )
        (sample_dir / "index.html").write_text(detail, encoding="utf-8")
        index_sections.append(
            f'<li><a href="{escape(sample_id, quote=True)}/index.html">{escape(sample_id)}</a>'
            f" — {escape(str(row['reason']))}</li>"
        )

    unload_messages = []
    for model in models:
        if "unload_error" in model:
            error = model["unload_error"]
            unload_messages.append(
                f'<li>{escape(str(model["model_id"]))} 卸载失败：'
                f'{escape(error["type"])}：{escape(error["message"])}</li>'
            )
    if "unload_error" in detector:
        error = detector["unload_error"]
        unload_messages.append(
            '<li>检测器卸载失败：'
            f'{escape(error["type"])}：{escape(error["message"])}</li>'
        )
    failures = "<ul>" + "".join(unload_messages) + "</ul>" if unload_messages else "<p>无</p>"
    index = "\n".join(
        [
            '<!doctype html><html lang="zh-CN"><meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width,initial-scale=1">',
            "<title>同输入恢复候选对照</title>",
            "<style>body{font:16px sans-serif;max-width:1100px;"
            "margin:2rem auto;padding:1rem}</style>",
            "<h1>同输入恢复候选对照</h1>",
            "<p>离线辅助证据。保留/丢失/新增仅描述检测框一致性，不是真值 FP/FN；"
            "ROI 检测不计算全图准确率。</p>",
            '<p><a href="results.json">完整结果 JSON</a></p>',
            "<h2>样本</h2><ul>",
            *index_sections,
            "</ul><h2>卸载失败</h2>",
            failures,
            "</html>",
        ]
    )
    (output / "index.html").write_text(index, encoding="utf-8")


def evaluate(
    manifest: Path,
    models: Path,
    output: Path,
    *,
    detector_checkpoint: Path,
    device: str = "cuda:0",
    model_factory: Callable[[str, Path, str], _EnhancementAdapter] | None = None,
    detector_factory: Callable[..., Any] | None = None,
    cuda_probe_factory: Callable[[str], _CudaProbeProtocol] | None = None,
) -> dict[str, Any]:
    """Run a new offline comparison directory; existing evidence is never overwritten."""
    manifest = Path(manifest)
    models = Path(models)
    output = Path(output)
    detector_checkpoint = Path(detector_checkpoint)
    if output.exists():
        raise FileExistsError("Use a new output directory; previous evidence is never overwritten")
    parsed = _load_manifest(manifest, models)
    prepared_samples, prepared_models = _prepare_inputs(parsed, manifest, models)

    if model_factory is None:
        from pipe_inspector.vision.restoration_candidates import make_restoration_model

        model_factory = make_restoration_model
    if detector_factory is None:
        from pipe_inspector.vision.detector import UltralyticsDetector

        detector_factory = UltralyticsDetector
    if cuda_probe_factory is None:
        cuda_probe_factory = _CudaProbe
    cuda_probe = cuda_probe_factory(device)

    output.mkdir(parents=True, exist_ok=False)
    rows = []
    for prepared in prepared_samples:
        row, _ = _save_controls(output, prepared)
        rows.append(row)

    model_records = _run_models(
        output,
        prepared_samples,
        prepared_models,
        rows,
        device=device,
        model_factory=model_factory,
        cuda_probe=cuda_probe,
    )
    _add_aligned_detail_crops(output, rows)
    detector_record = _detect_artifacts(
        output,
        rows,
        detector_checkpoint=detector_checkpoint,
        device=device,
        detector_factory=detector_factory,
    )
    artifacts = [artifact for row in rows for artifact in _all_artifacts(row)]
    jobs = [job for row in rows for job in row["models"].values()]
    summary = {
        "sample_count": len(rows),
        "model_count": len(model_records),
        "enhancement_succeeded": sum(job["status"] == "succeeded" for job in jobs),
        "enhancement_failed": sum(job["status"] == "failed" for job in jobs),
        "detector_succeeded": sum(
            artifact.get("detector_status") == "succeeded" for artifact in artifacts
        ),
        "detector_failed": sum(
            artifact.get("detector_status") == "failed" for artifact in artifacts
        ),
        "unload_failures": sum("unload_error" in model for model in model_records)
        + int("unload_error" in detector_record),
    }
    result = {
        "schema_version": "restoration-comparison-v1",
        "device": device,
        "operating_point": {
            "detector_imgsz": DETECTOR_IMGSZ,
            "detector_conf": DETECTOR_CONF,
            "match_iou": MATCH_IOU,
            "strong_baseline_score": STRONG_BASELINE_SCORE,
        },
        "summary": summary,
        "detector": detector_record,
        "models": model_records,
        "samples": rows,
    }
    (output / "results.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _write_gallery(output, rows, model_records, detector_record)
    return result


def regenerate_derived(output: Path) -> dict[str, Any]:
    """Refresh comparisons, fixed-ROI crops, and gallery without model or detector calls."""
    output = Path(output)
    results_path = output / "results.json"
    if not output.is_dir() or not results_path.is_file():
        raise FileNotFoundError(f"existing restoration results do not exist: {results_path}")
    original_bytes = results_path.read_bytes()
    result = json.loads(original_bytes)
    if not isinstance(result, dict) or result.get("schema_version") != "restoration-comparison-v1":
        raise ValueError("results.json is not a restoration-comparison-v1 result")
    rows = result.get("samples")
    models = result.get("models")
    detector = result.get("detector")
    summary = result.get("summary")
    if not all(isinstance(value, expected) for value, expected in (
        (rows, list),
        (models, list),
        (detector, dict),
        (summary, dict),
    )):
        raise ValueError("results.json has invalid restoration result sections")

    backup = output / "results-before-derived-refresh.json"
    if not backup.exists():
        backup.write_bytes(original_bytes)
    _add_aligned_detail_crops(output, rows)
    _rebuild_comparisons(rows)
    summary["unload_failures"] = sum("unload_error" in model for model in models) + int(
        "unload_error" in detector
    )
    _write_artifact_json(output, rows)
    temporary = output / "results.derived-refresh.tmp"
    temporary.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(results_path)
    _write_gallery(output, rows, models, detector)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--models", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--detector-checkpoint", required=True, type=Path)
    parser.add_argument("--device", default="cuda:0")
    args = parser.parse_args(argv)
    result = evaluate(
        args.manifest,
        args.models,
        args.output,
        detector_checkpoint=args.detector_checkpoint,
        device=args.device,
    )
    print(json.dumps(result["summary"], ensure_ascii=False))
    failed = (
        result["summary"]["enhancement_failed"]
        + result["summary"]["detector_failed"]
        + result["summary"]["unload_failures"]
    )
    return int(failed > 0)


if __name__ == "__main__":
    raise SystemExit(main())
