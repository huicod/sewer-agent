"""Lazy, local-only adapters for pinned prompted-mask and relative-depth models."""

from __future__ import annotations

import gc
import importlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
from PIL import Image

from pipe_inspector.contracts.common import ImageRegion
from pipe_inspector.vision.geometry import MaskPrediction

SAM2_REVISION = "665f8e2ad61cf5f53d65644ff27c8ee525124610"
DEPTH_ANYTHING_V2_REVISION = "5426e4f0f36572d16453bbda7a8389317b1bef99"
MAX_DEPTH_PROCESSED_PIXELS = 2_097_152
_DEPTH_INPUT_SIZE = 518
_DEPTH_PATCH_SIZE = 14


@dataclass(frozen=True, slots=True)
class _ModelSpec:
    repo_id: str
    revision: str
    model_type: str
    assets: tuple[str, ...]


_SAM2_SPEC = _ModelSpec(
    repo_id="facebook/sam2.1-hiera-large",
    revision=SAM2_REVISION,
    model_type="sam2_video",
    assets=(
        "model.safetensors",
        "config.json",
        "preprocessor_config.json",
        "processor_config.json",
        "video_preprocessor_config.json",
    ),
)
_DEPTH_SPEC = _ModelSpec(
    repo_id="depth-anything/Depth-Anything-V2-Small-hf",
    revision=DEPTH_ANYTHING_V2_REVISION,
    model_type="depth_anything",
    assets=("model.safetensors", "config.json", "preprocessor_config.json"),
)


def _validate_rgb_image(image: Image.Image) -> tuple[int, int]:
    if not isinstance(image, Image.Image):
        raise TypeError("image must be a PIL.Image.Image")
    if image.mode != "RGB":
        raise ValueError("geometry model input must be an RGB image")
    width, height = image.size
    if width <= 0 or height <= 0:
        raise ValueError("geometry model input dimensions must be positive")
    return width, height


def _validate_checkpoint(checkpoint: Path, spec: _ModelSpec) -> None:
    if not checkpoint.is_dir():
        raise FileNotFoundError(f"Hugging Face checkpoint does not exist: {checkpoint}")
    setup = (
        f"provision {spec.repo_id}@{spec.revision} and its provenance.json"
    )
    provenance_path = checkpoint / "provenance.json"
    if not provenance_path.is_file():
        raise FileNotFoundError(f"checkpoint provenance.json is missing; {setup}")
    try:
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"checkpoint provenance.json is invalid; {setup}") from exc
    expected = {"repo_id": spec.repo_id, "revision": spec.revision}
    if provenance != expected:
        raise ValueError(
            f"checkpoint provenance is {provenance!r}, expected {expected!r}; {setup}"
        )
    for filename in spec.assets:
        asset = checkpoint / filename
        if not asset.is_file():
            raise FileNotFoundError(f"checkpoint asset {filename} is missing; {setup}")


def _common_load_args(spec: _ModelSpec) -> dict[str, Any]:
    return {
        "revision": spec.revision,
        "local_files_only": True,
        "trust_remote_code": False,
    }


def _validate_model_type(config: Any, spec: _ModelSpec) -> None:
    model_type = getattr(config, "model_type", None)
    if model_type != spec.model_type:
        raise ValueError(
            f"checkpoint model_type must be {spec.model_type!r}, got {model_type!r}"
        )


def _strict_loaded_model(loaded: Any) -> Any:
    if not isinstance(loaded, tuple) or len(loaded) != 2:
        raise ValueError("model loader did not return weight loading diagnostics")
    model, loading_info = loaded
    if not isinstance(loading_info, Mapping):
        raise ValueError("model loader returned invalid weight loading diagnostics")
    failures = {
        key: loading_info.get(key)
        for key in ("missing_keys", "unexpected_keys", "mismatched_keys", "error_msgs")
        if loading_info.get(key)
    }
    if failures:
        raise ValueError(f"checkpoint weights did not load strictly: {failures!r}")
    return model


def _to_numpy(value: Any, *, label: str) -> np.ndarray:
    try:
        return value.detach().cpu().numpy()
    except (AttributeError, TypeError, RuntimeError) as exc:
        raise ValueError(f"{label} must be a tensor") from exc


class _LocalGeometryModel:
    def __init__(self, checkpoint: Path, device: str, spec: _ModelSpec) -> None:
        self.checkpoint = Path(checkpoint)
        self.device = device
        self.model_id = spec.repo_id
        self.model_version = spec.revision
        self.precision = "float32"
        self._spec = spec
        self._processor: Any | None = None
        self._model: Any | None = None
        self._torch: Any | None = None

    def unload(self) -> None:
        """Release model state; a later prediction reloads the pinned snapshot."""
        model = self._model
        torch = self._torch
        self._model = None
        self._processor = None
        self._torch = None
        if model is None:
            return
        try:
            model.cpu()
        finally:
            del model
            gc.collect()
            if torch is not None and self.device.startswith("cuda"):
                torch.cuda.empty_cache()


class LocalSegmenter(_LocalGeometryModel):
    """SAM 2.1 Hiera Large box-prompt adapter over one original RGB raster."""

    def __init__(self, checkpoint: Path, device: str = "cuda:0") -> None:
        super().__init__(checkpoint, device, _SAM2_SPEC)

    def segment_image(self, image: Image.Image, prompt: ImageRegion) -> MaskPrediction:
        width, height = _validate_rgb_image(image)
        if not isinstance(prompt, ImageRegion):
            raise TypeError("prompt must be an ImageRegion")
        if (prompt.width, prompt.height) != (width, height):
            raise ValueError("prompt dimensions differ from model input image")
        self._ensure_loaded()
        assert self._processor is not None
        assert self._model is not None
        assert self._torch is not None

        box = [float(value) for value in prompt.bbox_xyxy]
        inputs = self._processor(
            images=image,
            input_boxes=[[box]],
            return_tensors="pt",
        )
        inputs.to(device=self.device, dtype=self._torch.float32)
        with self._torch.inference_mode():
            outputs = self._model(**inputs, multimask_output=False)

        try:
            restored = self._processor.post_process_masks(
                outputs.pred_masks,
                inputs["original_sizes"],
                mask_threshold=0.0,
                binarize=True,
            )
            score_values = _to_numpy(outputs.iou_scores, label="SAM mask score")
        except (AttributeError, KeyError, TypeError, IndexError) as exc:
            raise ValueError("SAM returned malformed mask output") from exc
        if not isinstance(restored, list) or len(restored) != 1:
            raise ValueError("SAM must return one post-processed image raster")
        mask_values = _to_numpy(restored[0], label="SAM mask")
        if mask_values.shape != (1, 1, height, width) or mask_values.dtype != np.bool_:
            raise ValueError(
                "SAM mask has invalid shape or dtype; expected one boolean original-size raster"
            )
        if score_values.shape != (1, 1, 1):
            raise ValueError("SAM mask score has invalid shape")
        score = float(score_values[0, 0, 0])
        if not math.isfinite(score):
            raise ValueError("SAM mask score is nonfinite")
        return MaskPrediction(mask=mask_values[0, 0].copy(), score=score)

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        _validate_checkpoint(self.checkpoint, self._spec)
        torch = importlib.import_module("torch")
        transformers = importlib.import_module("transformers")
        common = _common_load_args(self._spec)
        processor = transformers.AutoProcessor.from_pretrained(str(self.checkpoint), **common)
        config = transformers.AutoConfig.from_pretrained(str(self.checkpoint), **common)
        _validate_model_type(config, self._spec)
        model = _strict_loaded_model(
            transformers.AutoModelForMaskGeneration.from_pretrained(
                str(self.checkpoint),
                **common,
                config=config,
                use_safetensors=True,
                dtype=torch.float32,
                output_loading_info=True,
            )
        )
        self._processor = processor
        self._model = model
        self._torch = torch
        model.to(self.device)
        model.eval()


def _depth_processed_size(width: int, height: int) -> tuple[int, int]:
    scale_height = _DEPTH_INPUT_SIZE / height
    scale_width = _DEPTH_INPUT_SIZE / width
    scale = (
        scale_width
        if abs(1.0 - scale_width) < abs(1.0 - scale_height)
        else scale_height
    )
    processed_height = round(scale * height / _DEPTH_PATCH_SIZE) * _DEPTH_PATCH_SIZE
    processed_width = round(scale * width / _DEPTH_PATCH_SIZE) * _DEPTH_PATCH_SIZE
    return processed_width, processed_height


class LocalRelativeDepth(_LocalGeometryModel):
    """Depth Anything V2 Small disparity-like relative-depth adapter."""

    larger_is: Literal["nearer"] = "nearer"

    def __init__(self, checkpoint: Path, device: str = "cuda:0") -> None:
        super().__init__(checkpoint, device, _DEPTH_SPEC)

    def predict_depth(self, image: Image.Image) -> np.ndarray:
        width, height = _validate_rgb_image(image)
        processed_width, processed_height = _depth_processed_size(width, height)
        processed_pixels = processed_width * processed_height
        if (
            processed_width <= 0
            or processed_height <= 0
            or processed_pixels > MAX_DEPTH_PROCESSED_PIXELS
        ):
            raise ValueError(
                "Depth Anything processed pixel limit exceeded: "
                f"{processed_width}x{processed_height} > {MAX_DEPTH_PROCESSED_PIXELS:,}"
            )
        self._ensure_loaded()
        assert self._processor is not None
        assert self._model is not None
        assert self._torch is not None

        inputs = self._processor(images=image, return_tensors="pt")
        inputs.to(device=self.device, dtype=self._torch.float32)
        with self._torch.inference_mode():
            outputs = self._model(**inputs)
        try:
            restored = self._processor.post_process_depth_estimation(
                outputs,
                target_sizes=[(height, width)],
            )
            values = _to_numpy(restored[0]["predicted_depth"], label="relative depth")
        except (AttributeError, KeyError, TypeError, IndexError) as exc:
            raise ValueError("Depth Anything returned malformed output") from exc
        if values.shape != (height, width) or not np.issubdtype(values.dtype, np.floating):
            raise ValueError(
                "relative depth has invalid shape or dtype; expected one original-size float raster"
            )
        if not np.isfinite(values).all():
            raise ValueError("relative depth contains nonfinite values")
        return values.astype(np.float32, copy=True)

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        _validate_checkpoint(self.checkpoint, self._spec)
        torch = importlib.import_module("torch")
        transformers = importlib.import_module("transformers")
        common = _common_load_args(self._spec)
        processor = transformers.AutoImageProcessor.from_pretrained(
            str(self.checkpoint), **common, use_fast=False
        )
        config = transformers.AutoConfig.from_pretrained(str(self.checkpoint), **common)
        _validate_model_type(config, self._spec)
        model = _strict_loaded_model(
            transformers.AutoModelForDepthEstimation.from_pretrained(
                str(self.checkpoint),
                **common,
                config=config,
                use_safetensors=True,
                dtype=torch.float32,
                output_loading_info=True,
            )
        )
        self._processor = processor
        self._model = model
        self._torch = torch
        model.to(self.device)
        model.eval()
