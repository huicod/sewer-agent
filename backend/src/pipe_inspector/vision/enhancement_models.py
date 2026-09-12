"""Lazy, local-only adapters for the approved enhancement checkpoints."""

from __future__ import annotations

import gc
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image
from torch import nn

from pipe_inspector.vision._enhancement_vendor import MCT, DarkIR, Restormer, SRVGGNetCompact

EnhancementKind = Literal["dehaze", "deblur", "super_resolution", "low_light"]
MAX_INPUT_PIXELS = 1_048_576
DEBLUR_TILE_SIZE = 512
DEBLUR_TILE_OVERLAP = 32
SUPER_RESOLUTION_TILE_SIZE = 256
SUPER_RESOLUTION_TILE_PAD = 10


@dataclass(frozen=True, slots=True)
class _ModelSpec:
    model_id: str
    scale: int
    checkpoint_keys: tuple[str, ...]


_MODEL_SPECS: dict[str, _ModelSpec] = {
    "dehaze": _ModelSpec(
        model_id="dehazeformer-mct",
        scale=1,
        checkpoint_keys=("state_dict",),
    ),
    "deblur": _ModelSpec(
        model_id="restormer-motion-deblurring",
        scale=1,
        checkpoint_keys=("params",),
    ),
    "super_resolution": _ModelSpec(
        model_id="realesr-general-x4v3",
        scale=4,
        checkpoint_keys=("params_ema", "params"),
    ),
    "low_light": _ModelSpec(
        model_id="darkir-384",
        scale=1,
        checkpoint_keys=("params",),
    ),
}


def _create_model(kind: str) -> nn.Module:
    if kind == "dehaze":
        return MCT()
    if kind == "deblur":
        return Restormer(
            inp_channels=3,
            out_channels=3,
            dim=48,
            num_blocks=(4, 6, 6, 8),
            num_refinement_blocks=4,
            heads=(1, 2, 4, 8),
            ffn_expansion_factor=2.66,
            bias=False,
            layer_norm_type="WithBias",
            dual_pixel_task=False,
        )
    if kind == "super_resolution":
        return SRVGGNetCompact(
            num_in_ch=3,
            num_out_ch=3,
            num_feat=64,
            num_conv=32,
            upscale=4,
            act_type="prelu",
        )
    if kind == "low_light":
        return DarkIR()
    raise ValueError(f"unsupported enhancement kind: {kind!r}")


class LocalEnhancementModel:
    """Run one pinned enhancement architecture from a local checkpoint."""

    def __init__(
        self,
        kind: str,
        checkpoint: Path,
        device: str = "cuda:0",
    ) -> None:
        normalized_kind = str(kind)
        try:
            spec = _MODEL_SPECS[normalized_kind]
        except KeyError as exc:
            raise ValueError(f"unsupported enhancement kind: {normalized_kind!r}") from exc

        self.kind = normalized_kind
        self.checkpoint = Path(checkpoint)
        self.device = device
        self.model_id = spec.model_id
        self.model_version = self.checkpoint.name
        self.scale = spec.scale
        self._spec = spec
        self._model: nn.Module | None = None

    def enhance_image(self, image: Image.Image) -> Image.Image:
        tensor = self._image_to_tensor(image)
        model = self._ensure_model()
        with torch.inference_mode():
            if self.kind == "dehaze":
                output = model(tensor * 2.0 - 1.0)
                output = self._validate_output(
                    output,
                    expected_height=tensor.shape[-2],
                    expected_width=tensor.shape[-1],
                )
                output = output * 0.5 + 0.5
            elif self.kind == "deblur":
                output = self._deblur_tiled(model, tensor)
            elif self.kind == "super_resolution":
                output = self._super_resolve_tiled(model, tensor)
            else:
                output = model(tensor)

        output = self._validate_output(
            output,
            expected_height=tensor.shape[-2] * self.scale,
            expected_width=tensor.shape[-1] * self.scale,
        ).clamp(0.0, 1.0)
        return self._tensor_to_image(output)

    def unload(self) -> None:
        """Release this adapter's model reference and any CUDA allocator cache."""
        model, self._model = self._model, None
        if model is None:
            return
        try:
            model.cpu()
        finally:
            del model
            gc.collect()
            if torch.device(self.device).type == "cuda" and torch.cuda.is_available():
                torch.cuda.empty_cache()

    def _ensure_model(self) -> nn.Module:
        if self._model is not None:
            return self._model
        if not self.checkpoint.is_file():
            raise FileNotFoundError(f"enhancement checkpoint does not exist: {self.checkpoint}")

        payload = torch.load(self.checkpoint, weights_only=True, map_location="cpu")
        state_dict = self._extract_state_dict(payload)
        model = _create_model(self.kind)
        model.load_state_dict(state_dict, strict=True)
        model.to(torch.device(self.device))
        model.eval()
        self._model = model
        return model

    def _extract_state_dict(self, payload: object) -> Mapping[str, Any]:
        if not isinstance(payload, Mapping):
            raise ValueError(
                f"{self.model_id} checkpoint must contain a named state dict"
            )
        for key in self._spec.checkpoint_keys:
            value = payload.get(key)
            if isinstance(value, Mapping):
                return value
        expected = " or ".join(repr(key) for key in self._spec.checkpoint_keys)
        raise ValueError(
            f"{self.model_id} checkpoint must contain state dict key {expected}"
        )

    def _image_to_tensor(self, image: Image.Image) -> torch.Tensor:
        if not isinstance(image, Image.Image):
            raise TypeError("image must be a PIL.Image.Image")
        width, height = image.size
        if width <= 0 or height <= 0:
            raise ValueError("input image dimensions must be positive")
        pixels = width * height
        if pixels > MAX_INPUT_PIXELS:
            raise ValueError(
                f"input image exceeds the {MAX_INPUT_PIXELS:,} pixels limit: {pixels:,}"
            )

        rgb = image.convert("RGB")
        array = np.array(rgb, dtype=np.float32, copy=True) / 255.0
        if array.shape != (height, width, 3) or not np.isfinite(array).all():
            raise ValueError("input image did not produce finite RGB values")
        tensor = torch.from_numpy(array).permute(2, 0, 1).unsqueeze(0)
        return tensor.to(device=torch.device(self.device), dtype=torch.float32)

    @staticmethod
    def _validate_output(
        output: object,
        *,
        expected_height: int,
        expected_width: int,
    ) -> torch.Tensor:
        if not isinstance(output, torch.Tensor):
            raise ValueError("enhancement model output must be a tensor")
        if output.ndim != 4 or output.shape[0] != 1 or output.shape[1] != 3:
            raise ValueError(
                "enhancement model output has invalid shape; expected one RGB tensor, "
                f"got {tuple(output.shape)}"
            )
        if output.shape[-2:] != (expected_height, expected_width):
            raise ValueError(
                "enhancement model output has invalid size; expected "
                f"{expected_width}x{expected_height}, got "
                f"{output.shape[-1]}x{output.shape[-2]}"
            )
        if not torch.isfinite(output).all().item():
            raise ValueError("enhancement model output contains nonfinite values")
        return output.to(dtype=torch.float32)

    def _deblur_tiled(self, model: nn.Module, image: torch.Tensor) -> torch.Tensor:
        _, _, height, width = image.shape
        output = torch.zeros_like(image)
        weights = torch.zeros((1, 1, height, width), device=image.device, dtype=image.dtype)
        stride = DEBLUR_TILE_SIZE - DEBLUR_TILE_OVERLAP
        for top in range(0, height, stride):
            bottom = min(top + DEBLUR_TILE_SIZE, height)
            for left in range(0, width, stride):
                right = min(left + DEBLUR_TILE_SIZE, width)
                tile = image[..., top:bottom, left:right]
                padded, original_height, original_width = self._pad_to_multiple(tile, multiple=8)
                restored = self._validate_output(
                    model(padded),
                    expected_height=padded.shape[-2],
                    expected_width=padded.shape[-1],
                )[..., :original_height, :original_width]
                output[..., top:bottom, left:right] += restored
                weights[..., top:bottom, left:right] += 1
        return output / weights

    def _super_resolve_tiled(self, model: nn.Module, image: torch.Tensor) -> torch.Tensor:
        _, _, height, width = image.shape
        scale = self.scale
        output = torch.empty(
            (1, 3, height * scale, width * scale),
            device=image.device,
            dtype=image.dtype,
        )
        for top in range(0, height, SUPER_RESOLUTION_TILE_SIZE):
            bottom = min(top + SUPER_RESOLUTION_TILE_SIZE, height)
            padded_top = max(0, top - SUPER_RESOLUTION_TILE_PAD)
            padded_bottom = min(height, bottom + SUPER_RESOLUTION_TILE_PAD)
            for left in range(0, width, SUPER_RESOLUTION_TILE_SIZE):
                right = min(left + SUPER_RESOLUTION_TILE_SIZE, width)
                padded_left = max(0, left - SUPER_RESOLUTION_TILE_PAD)
                padded_right = min(width, right + SUPER_RESOLUTION_TILE_PAD)
                tile = image[..., padded_top:padded_bottom, padded_left:padded_right]
                enhanced = self._validate_output(
                    model(tile),
                    expected_height=tile.shape[-2] * scale,
                    expected_width=tile.shape[-1] * scale,
                )
                crop_top = (top - padded_top) * scale
                crop_bottom = crop_top + (bottom - top) * scale
                crop_left = (left - padded_left) * scale
                crop_right = crop_left + (right - left) * scale
                output[
                    ...,
                    top * scale : bottom * scale,
                    left * scale : right * scale,
                ] = enhanced[..., crop_top:crop_bottom, crop_left:crop_right]
        return output

    @staticmethod
    def _pad_to_multiple(
        image: torch.Tensor,
        *,
        multiple: int,
    ) -> tuple[torch.Tensor, int, int]:
        height, width = image.shape[-2:]
        pad_bottom = (multiple - height % multiple) % multiple
        pad_right = (multiple - width % multiple) % multiple
        if pad_bottom == 0 and pad_right == 0:
            return image, height, width
        mode = "reflect" if height > pad_bottom and width > pad_right else "replicate"
        padded = F.pad(image, (0, pad_right, 0, pad_bottom), mode=mode)
        return padded, height, width

    @staticmethod
    def _tensor_to_image(output: torch.Tensor) -> Image.Image:
        array = output[0].detach().cpu().permute(1, 2, 0).numpy()
        array = np.rint(array * 255.0).astype(np.uint8)
        return Image.fromarray(array)
