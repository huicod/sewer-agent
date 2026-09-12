"""Local-only restoration challengers for offline evaluation, not the online registry."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import torch
from PIL import Image
from torch import nn

from pipe_inspector.vision.enhancement import ENHANCEMENT_MODELS
from pipe_inspector.vision.enhancement_models import LocalEnhancementModel


@dataclass(frozen=True)
class CandidateSpec:
    kind: str
    scale: int
    multiple: int
    checkpoint_key: str


CANDIDATE_SPECS = {
    "concertormer-realblur-j": CandidateSpec("deblur", 1, 64, "params"),
    "convir-l-haze4k": CandidateSpec("dehaze", 1, 32, "model"),
    "hat-l-x4-imagenet": CandidateSpec("super_resolution", 4, 16, "params_ema"),
}


def _build_candidate(model_id: str) -> nn.Module:
    if model_id == "concertormer-realblur-j":
        from pipe_inspector.vision._enhancement_vendor.concertormer import build_concertormer

        return build_concertormer()
    if model_id == "convir-l-haze4k":
        from pipe_inspector.vision._enhancement_vendor.convir import build_net

        return build_net("large", "Haze4k")
    if model_id == "hat-l-x4-imagenet":
        from pipe_inspector.vision._enhancement_vendor.hat import HAT

        return HAT(
            upscale=4,
            in_chans=3,
            img_size=64,
            window_size=16,
            compress_ratio=3,
            squeeze_factor=30,
            conv_scale=0.01,
            overlap_ratio=0.5,
            img_range=1.0,
            depths=(6,) * 12,
            embed_dim=180,
            num_heads=(6,) * 12,
            mlp_ratio=2,
            upsampler="pixelshuffle",
            resi_connection="1conv",
        )
    raise ValueError(f"unsupported restoration candidate: {model_id}")


class CandidateEnhancementModel(LocalEnhancementModel):
    """Reuse the existing RGB/size/lifecycle boundary with separate pinned architectures.

    Inference is FP32, single image, no ensemble or generated-detail fallback.
    ConvIR uses its final multiscale head; HAT handles its own mean normalization.
    The caller owns unload in a finally block, also when loading/inference fails.
    """

    def __init__(self, model_id: str, checkpoint: Path, device: str = "cuda:0") -> None:
        spec = CANDIDATE_SPECS[model_id]
        super().__init__(spec.kind, checkpoint, device)
        self.model_id = model_id
        self.scale = spec.scale
        self.candidate_spec = spec

    def _ensure_model(self) -> nn.Module:
        if self._model is not None:
            return self._model
        if not self.checkpoint.is_file():
            raise FileNotFoundError(f"local checkpoint missing: {self.checkpoint}")
        payload = torch.load(self.checkpoint, weights_only=True, map_location="cpu")
        key = self.candidate_spec.checkpoint_key
        if not isinstance(payload, Mapping) or not isinstance(payload.get(key), Mapping):
            raise ValueError(f"{self.model_id} checkpoint requires {key}")
        model = _build_candidate(self.model_id)
        model.load_state_dict(payload[key], strict=True)
        model.to(torch.device(self.device)).eval()
        self._model = model
        return model

    def enhance_image(self, image: Image.Image) -> Image.Image:
        if image.width * image.height * self.scale**2 > 4_194_304:
            raise ValueError("restoration output pixel limit exceeded")
        tensor = self._image_to_tensor(image)
        model = self._ensure_model()
        if self.model_id == "convir-l-haze4k" and min(image.size) < 256:
            # At the deepest 1/4 scale, pooling + dilated reflection needs >=256.
            raise ValueError("ConvIR Haze4K requires each input dimension >=256")
        padded, height, width = self._pad_to_multiple(
            tensor,
            multiple=self.candidate_spec.multiple,
        )
        with torch.inference_mode():
            output = model(padded)
        if self.model_id == "concertormer-realblur-j":
            if not isinstance(output, (tuple, list)) or len(output) != 4:
                raise ValueError("Concertormer must return four decoder heads")
            output = output[-1]
        if self.model_id == "convir-l-haze4k":
            if not isinstance(output, (tuple, list)) or len(output) != 3:
                raise ValueError("ConvIR must return three resolution heads")
            output = output[2]
        output = self._validate_output(
            output,
            expected_height=padded.shape[-2] * self.scale,
            expected_width=padded.shape[-1] * self.scale,
        )[..., : height * self.scale, : width * self.scale].clamp(0, 1)
        return self._tensor_to_image(output)


def make_restoration_model(
    model_id: str,
    checkpoint: Path,
    device: str = "cuda:0",
) -> LocalEnhancementModel:
    """Explicit offline whitelist; importing/creating a model does not load/download it."""
    if model_id in CANDIDATE_SPECS:
        return CandidateEnhancementModel(model_id, checkpoint, device)
    for kind, (known_id, _) in ENHANCEMENT_MODELS.items():
        if kind != "low_light" and model_id == known_id:
            return LocalEnhancementModel(kind, checkpoint, device)
    raise ValueError(f"unsupported restoration model: {model_id}")
