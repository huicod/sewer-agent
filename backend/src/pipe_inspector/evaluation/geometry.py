"""Bounded offline gallery for prompted masks and relative depth evidence."""

from __future__ import annotations

import argparse
import json
import shutil
import time
from collections.abc import Callable, Mapping, Sequence
from html import escape
from pathlib import Path
from typing import Any, Protocol

from PIL import Image, ImageDraw
from pydantic import Field, FiniteFloat, TypeAdapter, model_validator

from pipe_inspector.contracts.common import ContractModel, ImageRegion
from pipe_inspector.contracts.geometry import RelativeDepthRequest, SegmentationRequest
from pipe_inspector.media import MediaStore
from pipe_inspector.vision.geometry import (
    MAX_GEOMETRY_PIXELS,
    measure_geometry,
    run_relative_depth,
    run_segmentation,
)

EVALUATION_SCHEMA_VERSION = "geometry-evaluation-v1"
MASK_SCORE_LABEL = "model mask quality estimate; not defect confidence"
DEPTH_PREVIEW_LEGEND = "white=near, black=far"


class GeometrySample(ContractModel):
    sample_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    image: Path
    prompt_xyxy: tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat]
    prompt_source: str = Field(min_length=1)


class _Manifest(ContractModel):
    samples: tuple[GeometrySample, ...] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def unique_samples(self) -> "_Manifest":
        ids = [sample.sample_id for sample in self.samples]
        if len(ids) != len(set(ids)):
            raise ValueError("sample_id values must be unique")
        return self


class _Probe(Protocol):
    def begin(self) -> None: ...

    def finish(self) -> dict[str, Any]: ...


class _CudaProbe:
    """Best-effort real CUDA synchronization and peak allocation measurement."""

    def __init__(self, device: str) -> None:
        self._torch: Any | None = None
        self._device: Any | None = None
        self._active = False
        self._status = "not_applicable"
        self._error: dict[str, str] | None = None
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
        result: dict[str, Any] = {
            "status": self._status,
            "peak_allocated_bytes": peak,
            "synchronized": synchronized,
        }
        if self._error is not None:
            result["error"] = self._error
        self._active = False
        return result


def _error(error: Exception) -> dict[str, str]:
    return {
        "type": type(error).__name__,
        "code": str(getattr(error, "code", type(error).__name__)),
        "message": "\n".join([str(error), *getattr(error, "__notes__", [])]),
    }


def _resolve(path: Path, manifest: Path) -> Path:
    return path.resolve() if path.is_absolute() else (manifest.parent / path).resolve()


def _load_manifest(path: Path) -> _Manifest:
    samples = TypeAdapter(tuple[GeometrySample, ...]).validate_json(
        path.read_text(encoding="utf-8")
    )
    try:
        return _Manifest(samples=samples)
    except ValueError as error:
        if not 1 <= len(samples) <= 8:
            raise ValueError("Manifest requires 1..8 unique image samples") from error
        raise


def _timed_call(
    operation: Callable[[], Any],
    *,
    device: str,
    scope: str,
    probe_factory: Callable[[str], _Probe],
) -> tuple[Any | None, dict[str, Any]]:
    probe = probe_factory(device)
    started = time.perf_counter()
    probe.begin()
    result = None
    failure: Exception | None = None
    try:
        result = operation()
    except Exception as error:
        failure = error
    measurement = probe.finish()
    duration = time.perf_counter() - started
    call: dict[str, Any] = {
        "status": "failed" if failure is not None else "succeeded",
        "duration_seconds": duration,
        "timing": {
            "scope": scope,
            "includes_lazy_model_load": True,
            "cuda_synchronized": measurement["synchronized"],
        },
        "cuda_peak_allocated_bytes": measurement["peak_allocated_bytes"],
        "cuda_measurement_status": measurement["status"],
    }
    if "error" in measurement:
        call["cuda_measurement_error"] = measurement["error"]
    if failure is not None:
        call["error"] = _error(failure)
    return result, call


def _copy_asset(media: MediaStore, ref, destination: Path) -> None:
    shutil.copyfile(media.path_for(ref), destination)


def _save_prompt_box(source: Path, destination: Path, prompt: ImageRegion) -> None:
    with Image.open(source) as opened:
        image = opened.convert("RGB")
    draw = ImageDraw.Draw(image)
    width = max(1, round(max(image.size) / 320))
    draw.rectangle(tuple(prompt.bbox_xyxy), outline="#facc15", width=width)
    image.save(destination, format="PNG")
    image.close()


def _sample_status(calls: Mapping[str, Mapping[str, Any]]) -> str:
    if calls["source"]["status"] != "succeeded":
        return "failed"
    successful_tools = sum(
        calls[name]["status"] == "succeeded" for name in ("segmentation", "depth")
    )
    if successful_tools == 2 and calls["measurement"]["status"] == "succeeded":
        return "succeeded"
    return "partial" if successful_tools else "failed"


def _image_markup(path: str, title: str) -> str:
    safe_path = escape(path, quote=True)
    return (
        f'<figure><figcaption>{escape(title)}</figcaption><a href="{safe_path}">'
        f'<img src="{safe_path}" alt="{escape(title, quote=True)}"></a></figure>'
    )


def _gallery(samples: Sequence[Mapping[str, Any]]) -> str:
    sections = []
    for row in samples:
        sample_id = str(row["sample_id"])
        prefix = f"{sample_id}/"
        cards = []
        gallery = row.get("gallery", {})
        for key, title in (
            ("original", "Original bytes"),
            ("prompt_box", "Prompt box on original pixels"),
            ("mask", "Binary predicted mask"),
            ("overlay", "Predicted mask overlay"),
            ("depth_preview", "Relative depth — white near, black far"),
        ):
            if key in gallery:
                cards.append(_image_markup(prefix + gallery[key], title))
        failures = []
        for name, call in row["calls"].items():
            if call["status"] == "failed":
                error = call["error"]
                failures.append(
                    f"<li>{escape(name)}: {escape(error['type'])}: "
                    f"{escape(error['message'])}</li>"
                )
            elif call["status"] == "skipped":
                failures.append(
                    f"<li>{escape(name)}: skipped ({escape(call['reason'])})</li>"
                )
        notes = "" if not failures else "<ul class=failures>" + "".join(failures) + "</ul>"
        sections.append(
            f'<section><h2>{escape(sample_id)} — {escape(str(row["status"]))}</h2>'
            f'<p>Prompt provenance: {escape(str(row["prompt_source"]))}</p>{notes}'
            f'<div class="grid">{"".join(cards)}</div>'
            f'<p><a href="{prefix}result.json">Per-sample result JSON</a></p></section>'
        )
    return "\n".join(
        [
            '<!doctype html><html lang="en"><meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width,initial-scale=1">',
            "<title>Offline geometry evidence</title><style>body{font:16px sans-serif;"
            "max-width:1300px;margin:2rem auto;padding:1rem}.grid{display:grid;"
            "grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:1rem}"
            "figure{margin:0}img{width:100%;max-height:520px;object-fit:contain;"
            "background:#111}.failures{color:#b91c1c}section{border-top:1px solid #aaa;"
            "margin-top:2rem}</style>",
            "<h1>Offline prompted-mask and relative-depth evidence</h1>",
            '<p>Prompts use original-image pixel coordinates. A prompt box is not mask truth. '
            "The mask score is a model quality estimate, not defect confidence. Relative depth "
            "uses per-image model units only and cannot be compared across images. Image-plane "
            "fractions and pixel sizes are not calibrated physical dimensions.</p>",
            '<p>Depth legend: white = near, black = far. <a href="results.json">Full JSON</a></p>',
            *sections,
            "</html>",
        ]
    )


def _write_results(output: Path, payload: Mapping[str, Any]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    (output / "results.json").write_text(encoded, encoding="utf-8")
    (output / "index.html").write_text(_gallery(payload["samples"]), encoding="utf-8")


def evaluate(
    manifest: Path,
    segmentation_checkpoint: Path,
    depth_checkpoint: Path,
    output: Path,
    *,
    device: str = "cuda:0",
    segmenter_factory: Callable[[Path, str], Any] | None = None,
    depth_factory: Callable[[Path, str], Any] | None = None,
    cuda_probe_factory: Callable[[str], _Probe] | None = None,
) -> dict[str, int]:
    """Run both independent tools per image and write a never-overwritten gallery."""
    if output.exists():
        raise FileExistsError("Use a new output directory; existing evidence is never overwritten")
    parsed = _load_manifest(manifest)
    segmentation_checkpoint = segmentation_checkpoint.resolve()
    depth_checkpoint = depth_checkpoint.resolve()
    for label, checkpoint in (
        ("segmentation", segmentation_checkpoint),
        ("depth", depth_checkpoint),
    ):
        if not checkpoint.is_dir():
            raise FileNotFoundError(
                f"Required local {label} checkpoint directory missing: {checkpoint}"
            )
    if segmenter_factory is None or depth_factory is None:
        from pipe_inspector.vision.geometry_models import LocalRelativeDepth, LocalSegmenter

        segmenter_factory = segmenter_factory or LocalSegmenter
        depth_factory = depth_factory or LocalRelativeDepth
    probe_factory = cuda_probe_factory or _CudaProbe
    output.mkdir(parents=True, exist_ok=False)
    media = MediaStore(
        output / "assets",
        max_pixels=MAX_GEOMETRY_PIXELS,
    )
    rows: list[dict[str, Any]] = []
    payload: dict[str, Any] = {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "device": device,
        "checkpoints": {
            "segmentation": str(segmentation_checkpoint),
            "depth": str(depth_checkpoint),
        },
        "summary": {"sample_count": 0, "succeeded": 0, "partial": 0, "failed": 0},
        "samples": rows,
    }

    for sample in parsed.samples:
        sample_dir = output / sample.sample_id
        sample_dir.mkdir()
        image_path = _resolve(sample.image, manifest)
        row: dict[str, Any] = {
            "sample_id": sample.sample_id,
            "input_path": str(image_path),
            "prompt_source": sample.prompt_source,
            "prompt": {
                "coordinate_space": "original_pixels",
                "bbox_xyxy": [float(value) for value in sample.prompt_xyxy],
            },
            "gallery": {},
            "calls": {},
        }
        rows.append(row)
        source_started = time.perf_counter()
        try:
            with image_path.open("rb") as stream:
                source = media.ingest(stream)
            original_name = f"original{image_path.suffix}"
            _copy_asset(media, source.ref, sample_dir / original_name)
            row["input_asset"] = source.model_dump(mode="json")
            row["gallery"]["original"] = original_name
            row["calls"]["source"] = {
                "status": "succeeded",
                "duration_seconds": time.perf_counter() - source_started,
                "gallery_path": original_name,
            }
        except Exception as error:
            row["calls"] = {
                "source": {
                    "status": "failed",
                    "duration_seconds": time.perf_counter() - source_started,
                    "error": _error(error),
                },
                "segmentation": {"status": "skipped", "reason": "invalid_source"},
                "depth": {"status": "skipped", "reason": "invalid_source"},
                "measurement": {"status": "skipped", "reason": "invalid_source"},
            }
            row["status"] = "failed"
            (sample_dir / "result.json").write_text(
                json.dumps(row, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            payload["summary"]["sample_count"] = len(rows)
            payload["summary"]["failed"] += 1
            _write_results(output, payload)
            continue

        prompt: ImageRegion | None = None
        try:
            prompt = ImageRegion(
                width=source.width,
                height=source.height,
                bbox_xyxy=sample.prompt_xyxy,
            )
            row["prompt"] = prompt.model_dump(mode="json") | {
                "coordinate_space": "original_pixels"
            }
            _save_prompt_box(media.path_for(source.ref), sample_dir / "prompt-box.png", prompt)
            row["gallery"]["prompt_box"] = "prompt-box.png"
        except Exception as error:
            row["calls"]["segmentation"] = {
                "status": "failed",
                "checkpoint": str(segmentation_checkpoint),
                "error": _error(error),
            }

        segmentation = None
        if prompt is not None:

            def segment():
                adapter = segmenter_factory(segmentation_checkpoint, device)
                return run_segmentation(
                    SegmentationRequest(input_asset=source.ref, prompt=prompt),
                    source,
                    media,
                    adapter,
                )

            segmentation, call = _timed_call(
                segment,
                device=device,
                scope="adapter_factory+run_segmentation",
                probe_factory=probe_factory,
            )
            call["checkpoint"] = str(segmentation_checkpoint)
            call["model_score_label"] = MASK_SCORE_LABEL
            if segmentation is not None:
                call["result"] = segmentation.model_dump(mode="json")
                _copy_asset(media, segmentation.mask_asset.ref, sample_dir / "mask.png")
                _copy_asset(media, segmentation.overlay_asset.ref, sample_dir / "overlay.png")
                call["gallery_paths"] = {"mask": "mask.png", "overlay": "overlay.png"}
                row["gallery"].update(mask="mask.png", overlay="overlay.png")
            row["calls"]["segmentation"] = call

        def predict_depth():
            adapter = depth_factory(depth_checkpoint, device)
            return run_relative_depth(
                RelativeDepthRequest(input_asset=source.ref), source, media, adapter
            )

        depth, call = _timed_call(
            predict_depth,
            device=device,
            scope="adapter_factory+run_relative_depth",
            probe_factory=probe_factory,
        )
        call["checkpoint"] = str(depth_checkpoint)
        call["preview_legend"] = DEPTH_PREVIEW_LEGEND
        if depth is not None:
            call["result"] = depth.model_dump(mode="json")
            _copy_asset(media, depth.raw_asset.ref, sample_dir / "depth.npy")
            _copy_asset(media, depth.preview_asset.ref, sample_dir / "depth-preview.png")
            call["gallery_paths"] = {"raw": "depth.npy", "preview": "depth-preview.png"}
            row["gallery"].update(
                depth_raw="depth.npy", depth_preview="depth-preview.png"
            )
        row["calls"]["depth"] = call

        if segmentation is None:
            row["calls"]["measurement"] = {
                "status": "skipped",
                "reason": "segmentation_failed",
            }
        else:
            measurement_started = time.perf_counter()
            try:
                measurement = measure_geometry(segmentation, media, depth)
                measurement_payload = measurement.model_dump(mode="json")
                (sample_dir / "measurement.json").write_text(
                    json.dumps(measurement_payload, ensure_ascii=False, indent=2, sort_keys=True)
                    + "\n",
                    encoding="utf-8",
                )
                measurement_status = "succeeded" if depth is not None else "partial"
                row["calls"]["measurement"] = {
                    "status": measurement_status,
                    "duration_seconds": time.perf_counter() - measurement_started,
                    "result": measurement_payload,
                    "gallery_path": "measurement.json",
                }
                if depth is None:
                    row["calls"]["measurement"]["reason"] = "depth_failed_mask_only"
                row["gallery"]["measurement"] = "measurement.json"
            except Exception as error:
                row["calls"]["measurement"] = {
                    "status": "failed",
                    "duration_seconds": time.perf_counter() - measurement_started,
                    "error": _error(error),
                }

        row["status"] = _sample_status(row["calls"])
        payload["summary"]["sample_count"] = len(rows)
        payload["summary"][row["status"]] += 1
        (sample_dir / "result.json").write_text(
            json.dumps(row, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        _write_results(output, payload)
    return dict(payload["summary"])


def main(
    argv: Sequence[str] | None = None,
    *,
    segmenter_factory: Callable[[Path, str], Any] | None = None,
    depth_factory: Callable[[Path, str], Any] | None = None,
    cuda_probe_factory: Callable[[str], _Probe] | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--segmentation-checkpoint", required=True, type=Path)
    parser.add_argument("--depth-checkpoint", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--device", default="cuda:0")
    args = parser.parse_args(argv)
    summary = evaluate(
        args.manifest,
        args.segmentation_checkpoint,
        args.depth_checkpoint,
        args.output,
        device=args.device,
        segmenter_factory=segmenter_factory,
        depth_factory=depth_factory,
        cuda_probe_factory=cuda_probe_factory,
    )
    print(json.dumps(summary, sort_keys=True))
    return int(summary["partial"] > 0 or summary["failed"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
