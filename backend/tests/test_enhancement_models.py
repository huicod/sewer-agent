from __future__ import annotations

import math
from enum import StrEnum
from pathlib import Path
from typing import Any

import pytest
import torch
import torch.nn.functional as F
from PIL import Image
from pipe_inspector.vision import enhancement_models
from pipe_inspector.vision.enhancement_models import LocalEnhancementModel
from torch import nn


class EnhancementKind(StrEnum):
    DEHAZE = "dehaze"


class TrackingModel(nn.Module):
    def __init__(self, *, scale: int = 1, result: str = "valid") -> None:
        super().__init__()
        self.gain = nn.Parameter(torch.tensor(1.0))
        self.scale = scale
        self.result = result
        self.inputs: list[torch.Tensor] = []

    def forward(self, image: torch.Tensor) -> Any:
        self.inputs.append(image.detach().cpu())
        output = image * self.gain
        if self.scale != 1:
            output = F.interpolate(output, scale_factor=self.scale, mode="nearest")
        if self.result == "nonfinite":
            output = output.clone()
            output[..., 0, 0] = math.nan
        elif self.result == "wrong_channels":
            output = output[:, :2]
        elif self.result == "wrong_size":
            output = output[..., :-1, :]
        elif self.result == "not_tensor":
            return [output]
        elif self.result == "out_of_range":
            output = output + 2.0
        return output


def checkpoint_for(
    tmp_path: Path,
    *,
    kind: str,
    state_dict: dict[str, torch.Tensor] | None = None,
) -> Path:
    checkpoint = tmp_path / f"{kind}.pth"
    key = {
        "dehaze": "state_dict",
        "deblur": "params",
        "super_resolution": "params_ema",
    }[kind]
    torch.save({key: state_dict or TrackingModel().state_dict()}, checkpoint)
    return checkpoint


def install_tracking_factory(
    monkeypatch: pytest.MonkeyPatch,
    *,
    result: str = "valid",
) -> list[TrackingModel]:
    models: list[TrackingModel] = []

    def create_model(kind: str) -> TrackingModel:
        model = TrackingModel(
            scale=4 if kind == "super_resolution" else 1,
            result=result,
        )
        models.append(model)
        return model

    monkeypatch.setattr(enhancement_models, "_create_model", create_model)
    return models


@pytest.mark.parametrize(
    ("kind", "model_id", "scale"),
    [
        (EnhancementKind.DEHAZE, "dehazeformer-mct", 1),
        ("deblur", "restormer-motion-deblurring", 1),
        ("super_resolution", "realesr-general-x4v3", 4),
    ],
)
def test_adapter_exposes_pinned_model_metadata(
    tmp_path: Path,
    kind: str,
    model_id: str,
    scale: int,
) -> None:
    checkpoint = tmp_path / "release.pth"

    adapter = LocalEnhancementModel(kind, checkpoint, device="cpu")

    assert adapter.kind == str(kind)
    assert adapter.model_id == model_id
    assert adapter.model_version == "release.pth"
    assert adapter.scale == scale


def test_adapter_rejects_unsupported_kind(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unsupported enhancement kind"):
        LocalEnhancementModel("sharpen", tmp_path / "weights.pth", device="cpu")


def test_adapter_loads_local_checkpoint_lazily_with_safe_torch_options(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint = checkpoint_for(tmp_path, kind="deblur")
    models = install_tracking_factory(monkeypatch)
    calls: list[tuple[Path, dict[str, object]]] = []
    real_load = torch.load

    def tracking_load(path: Path, **kwargs: object) -> object:
        calls.append((path, kwargs))
        return real_load(path, **kwargs)

    monkeypatch.setattr(enhancement_models.torch, "load", tracking_load)
    adapter = LocalEnhancementModel("deblur", checkpoint, device="cpu")

    assert models == []
    output = adapter.enhance_image(Image.new("RGB", (5, 3), (20, 40, 60)))

    assert output.size == (5, 3)
    assert models[0].training is False
    assert calls == [(checkpoint, {"weights_only": True, "map_location": "cpu"})]

    adapter.enhance_image(Image.new("RGB", (5, 3)))
    assert len(models) == 1
    assert len(calls) == 1


def test_adapter_defers_missing_checkpoint_failure_until_inference(tmp_path: Path) -> None:
    adapter = LocalEnhancementModel("dehaze", tmp_path / "missing.pth", device="cpu")

    with pytest.raises(FileNotFoundError, match="checkpoint does not exist"):
        adapter.enhance_image(Image.new("RGB", (8, 8)))


@pytest.mark.parametrize(
    ("kind", "payload"),
    [
        ("dehaze", {"params": {}}),
        ("deblur", {"state_dict": {}}),
        ("super_resolution", {"state_dict": {}}),
    ],
)
def test_adapter_rejects_checkpoint_without_expected_state_dict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    payload: dict[str, object],
) -> None:
    checkpoint = tmp_path / "wrong.pth"
    torch.save(payload, checkpoint)
    install_tracking_factory(monkeypatch)
    adapter = LocalEnhancementModel(kind, checkpoint, device="cpu")

    with pytest.raises(ValueError, match="state dict"):
        adapter.enhance_image(Image.new("RGB", (8, 8)))


def test_super_resolution_prefers_params_ema_over_params(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint = tmp_path / "sr.pth"
    good_state = TrackingModel().state_dict()
    bad_state = {"unexpected": torch.tensor(1.0)}
    torch.save({"params_ema": good_state, "params": bad_state}, checkpoint)
    install_tracking_factory(monkeypatch)
    adapter = LocalEnhancementModel("super_resolution", checkpoint, device="cpu")

    output = adapter.enhance_image(Image.new("RGB", (8, 6), (16, 32, 48)))

    assert output.size == (32, 24)


def test_adapter_loads_state_dict_strictly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint = checkpoint_for(
        tmp_path,
        kind="deblur",
        state_dict={"unexpected": torch.tensor(1.0)},
    )
    install_tracking_factory(monkeypatch)
    adapter = LocalEnhancementModel("deblur", checkpoint, device="cpu")

    with pytest.raises(RuntimeError, match="state_dict"):
        adapter.enhance_image(Image.new("RGB", (8, 8)))


def test_adapter_converts_rgba_without_mutating_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint = checkpoint_for(tmp_path, kind="deblur")
    install_tracking_factory(monkeypatch)
    source = Image.new("RGBA", (7, 5), (12, 34, 56, 78))
    source_bytes = source.tobytes()
    adapter = LocalEnhancementModel("deblur", checkpoint, device="cpu")

    output = adapter.enhance_image(source)

    assert source.mode == "RGBA"
    assert source.tobytes() == source_bytes
    assert output.mode == "RGB"
    assert output.size == source.size
    assert output.getpixel((0, 0)) == (12, 34, 56)


def test_adapter_rejects_non_image_and_oversize_input_before_loading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint = checkpoint_for(tmp_path, kind="deblur")
    models = install_tracking_factory(monkeypatch)
    adapter = LocalEnhancementModel("deblur", checkpoint, device="cpu")

    with pytest.raises(TypeError, match="PIL.Image.Image"):
        adapter.enhance_image(object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="1,048,576 pixels"):
        adapter.enhance_image(Image.new("RGB", (1025, 1024)))

    assert models == []


@pytest.mark.parametrize(
    ("result", "message"),
    [
        ("not_tensor", "tensor"),
        ("wrong_channels", "shape"),
        ("wrong_size", "size"),
        ("nonfinite", "nonfinite"),
    ],
)
def test_adapter_rejects_invalid_model_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    result: str,
    message: str,
) -> None:
    checkpoint = checkpoint_for(tmp_path, kind="deblur")
    install_tracking_factory(monkeypatch, result=result)
    adapter = LocalEnhancementModel("deblur", checkpoint, device="cpu")

    with pytest.raises(ValueError, match=message):
        adapter.enhance_image(Image.new("RGB", (16, 16)))


def test_adapter_clamps_finite_model_output_to_unit_range(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint = checkpoint_for(tmp_path, kind="deblur")
    install_tracking_factory(monkeypatch, result="out_of_range")
    adapter = LocalEnhancementModel("deblur", checkpoint, device="cpu")

    output = adapter.enhance_image(Image.new("RGB", (8, 8)))

    assert output.getextrema() == ((255, 255), (255, 255), (255, 255))


def test_deblur_uses_bounded_tiles_and_legal_padding_for_tiny_images(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint = checkpoint_for(tmp_path, kind="deblur")
    models = install_tracking_factory(monkeypatch)
    adapter = LocalEnhancementModel("deblur", checkpoint, device="cpu")

    large_output = adapter.enhance_image(Image.new("RGB", (700, 600), (30, 60, 90)))
    tiny_output = adapter.enhance_image(Image.new("RGB", (1, 2), (10, 20, 30)))

    assert large_output.size == (700, 600)
    assert tiny_output.size == (1, 2)
    assert len(models[0].inputs) > 2
    assert all(tensor.shape[-2] <= 512 and tensor.shape[-1] <= 512 for tensor in models[0].inputs)
    assert models[0].inputs[-1].shape[-2:] == (8, 8)


def test_super_resolution_uses_padded_tiles_and_returns_exact_x4_shape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint = checkpoint_for(tmp_path, kind="super_resolution")
    models = install_tracking_factory(monkeypatch)
    adapter = LocalEnhancementModel("super_resolution", checkpoint, device="cpu")

    output = adapter.enhance_image(Image.new("RGB", (300, 270), (15, 30, 45)))

    assert output.size == (1200, 1080)
    assert len(models[0].inputs) == 4
    assert all(tensor.shape[-2] <= 276 and tensor.shape[-1] <= 276 for tensor in models[0].inputs)


def test_unload_releases_model_and_next_inference_loads_again(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint = checkpoint_for(tmp_path, kind="deblur")
    models = install_tracking_factory(monkeypatch)
    adapter = LocalEnhancementModel("deblur", checkpoint, device="cpu")
    image = Image.new("RGB", (8, 8))

    adapter.enhance_image(image)
    adapter.unload()
    adapter.enhance_image(image)

    assert len(models) == 2
