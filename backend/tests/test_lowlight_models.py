from __future__ import annotations

from pathlib import Path

import pytest
import torch
from PIL import Image
from pipe_inspector.vision import _enhancement_vendor as enhancement_vendor
from pipe_inspector.vision import enhancement_models
from pipe_inspector.vision.enhancement import ENHANCEMENT_MODELS
from pipe_inspector.vision.enhancement_models import LocalEnhancementModel
from torch import nn


class _TrackingLowLight(nn.Module):
    def __init__(self, *, nonfinite: bool = False) -> None:
        super().__init__()
        self.gain = nn.Parameter(torch.tensor(1.0))
        self.nonfinite = nonfinite

    def forward(self, image: torch.Tensor) -> torch.Tensor:
        output = image * self.gain
        if self.nonfinite:
            output = output.clone()
            output[..., 0, 0] = torch.nan
        return output


def _checkpoint(tmp_path: Path, state_dict: dict[str, torch.Tensor]) -> Path:
    checkpoint = tmp_path / "darkir.pt"
    torch.save({"params": state_dict}, checkpoint)
    return checkpoint


def test_low_light_registry_and_adapter_pin_the_official_darkir_checkpoint(
    tmp_path: Path,
) -> None:
    adapter = LocalEnhancementModel("low_light", tmp_path / "release.pt", device="cpu")

    assert ENHANCEMENT_MODELS["low_light"] == (
        "darkir-384",
        "darkir-384-hf-8a73ebf2.pt",
    )
    assert adapter.kind == "low_light"
    assert adapter.model_id == "darkir-384"
    assert adapter.model_version == "release.pt"
    assert adapter.scale == 1


def test_low_light_adapter_strictly_loads_params_and_preserves_odd_rgb_size(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[_TrackingLowLight] = []

    def create_model(kind: str) -> nn.Module:
        assert kind == "low_light"
        model = _TrackingLowLight()
        created.append(model)
        return model

    monkeypatch.setattr(enhancement_models, "_create_model", create_model)
    checkpoint = _checkpoint(tmp_path, _TrackingLowLight().state_dict())
    adapter = LocalEnhancementModel("low_light", checkpoint, device="cpu")
    source = Image.new("RGBA", (13, 9), (12, 34, 56, 78))

    output = adapter.enhance_image(source)

    assert output.mode == "RGB"
    assert output.size == (13, 9)
    assert output.getpixel((0, 0)) == (12, 34, 56)
    assert len(created) == 1

    wrong = _checkpoint(tmp_path, {"unexpected": torch.tensor(1.0)})
    with pytest.raises(RuntimeError, match="state_dict"):
        LocalEnhancementModel("low_light", wrong, device="cpu").enhance_image(
            Image.new("RGB", (8, 8))
        )


def test_low_light_adapter_rejects_nonfinite_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        enhancement_models,
        "_create_model",
        lambda kind: _TrackingLowLight(nonfinite=True),
    )
    checkpoint = _checkpoint(tmp_path, _TrackingLowLight(nonfinite=True).state_dict())
    adapter = LocalEnhancementModel("low_light", checkpoint, device="cpu")

    with pytest.raises(ValueError, match="nonfinite"):
        adapter.enhance_image(Image.new("RGB", (8, 8)))


@pytest.mark.parametrize("factory_name", ["DarkIR", "CIDNet"])
def test_official_low_light_architectures_return_finite_rgb_at_odd_size(
    factory_name: str,
) -> None:
    factory = getattr(enhancement_vendor, factory_name)
    model = factory().eval()
    image = torch.rand((1, 3, 17, 13), dtype=torch.float32)
    if factory_name == "CIDNet":
        model.trans.alpha_s = 1.0
        model.trans.alpha = 1.0
        model.trans.gated = True
        model.trans.gated2 = True

    with torch.inference_mode():
        output = model(image)

    assert output.shape == image.shape
    assert torch.isfinite(output).all().item()


def test_low_light_unload_releases_model_and_reloads_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[nn.Module] = []

    def create_model(kind: str) -> nn.Module:
        model = _TrackingLowLight()
        created.append(model)
        return model

    monkeypatch.setattr(enhancement_models, "_create_model", create_model)
    checkpoint = _checkpoint(tmp_path, _TrackingLowLight().state_dict())
    adapter = LocalEnhancementModel("low_light", checkpoint, device="cpu")
    image = Image.new("RGB", (8, 8))

    adapter.enhance_image(image)
    adapter.unload()
    adapter.enhance_image(image)

    assert len(created) == 2
