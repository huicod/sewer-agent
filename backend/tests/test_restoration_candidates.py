"""Offline restoration adapters: no production-registry changes or model downloads."""

import importlib

import numpy as np
import pytest
import torch
from PIL import Image
from torch import nn


def test_hat_architecture_preserves_x4_rgb_shape_and_loads_strictly():
    from pipe_inspector.vision._enhancement_vendor.hat import HAT

    model = HAT(
        img_size=16,
        window_size=4,
        embed_dim=30,
        depths=(2,),
        num_heads=(3,),
        upscale=4,
        upsampler="pixelshuffle",
        mlp_ratio=2,
    ).eval()
    state = model.state_dict()
    model.load_state_dict(state, strict=True)
    with torch.inference_mode():
        output = model(torch.rand(1, 3, 12, 16))
    assert output.shape == (1, 3, 48, 64)
    assert torch.isfinite(output).all()
    del state["conv_first.weight"]
    with pytest.raises(RuntimeError, match="Missing key"):
        model.load_state_dict(state, strict=True)


def test_factory_keeps_candidates_offline_and_loading_lazy(tmp_path):
    from pipe_inspector.vision.enhancement import ENHANCEMENT_MODELS
    from pipe_inspector.vision.restoration_candidates import make_restoration_model

    existing = dict(ENHANCEMENT_MODELS)
    for model_id, kind, scale in (
        ("concertormer-realblur-j", "deblur", 1),
        ("convir-l-haze4k", "dehaze", 1),
        ("hat-l-x4-imagenet", "super_resolution", 4),
    ):
        model = make_restoration_model(model_id, tmp_path / "absent.pth", "cpu")
        assert (model.model_id, model.kind, model.scale) == (model_id, kind, scale)
        with pytest.raises(FileNotFoundError):
            model.enhance_image(Image.new("RGB", (16, 16)))
        model.unload()
    assert ENHANCEMENT_MODELS == existing
    with pytest.raises(ValueError, match="unsupported"):
        make_restoration_model("arbitrary.module", tmp_path / "absent", "cpu")


class PixelModel(nn.Module):
    def __init__(self, scale, invalid=None):
        super().__init__()
        self.scale = scale
        self.invalid = invalid

    def forward(self, x):
        assert x.shape[-1] % 16 == 0 and x.shape[-2] % 16 == 0
        if self.invalid == "nan":
            return x * float("nan")
        if self.invalid == "shape":
            return x[:, :1]
        return x.repeat_interleave(self.scale, -1).repeat_interleave(self.scale, -2)


def test_adapter_pads_crops_and_does_not_change_rgb_values(monkeypatch, tmp_path):
    from pipe_inspector.vision.restoration_candidates import make_restoration_model

    model = make_restoration_model("hat-l-x4-imagenet", tmp_path / "unused.pth", "cpu")
    monkeypatch.setattr(model, "_ensure_model", lambda: PixelModel(4))
    original = Image.new("RGB", (19, 23), (17, 98, 201))
    before = original.tobytes()
    enhanced = model.enhance_image(original)
    assert enhanced.size == (76, 92)
    assert np.asarray(enhanced)[20, 20].tolist() == [17, 98, 201]
    assert original.tobytes() == before


@pytest.mark.parametrize("invalid", ["nan", "shape"])
def test_invalid_model_output_is_not_an_image(monkeypatch, tmp_path, invalid):
    from pipe_inspector.vision.restoration_candidates import make_restoration_model

    model = make_restoration_model("hat-l-x4-imagenet", tmp_path / "unused.pth", "cpu")
    monkeypatch.setattr(model, "_ensure_model", lambda: PixelModel(4, invalid))
    with pytest.raises(ValueError):
        model.enhance_image(Image.new("RGB", (16, 16)))


def test_candidate_rejects_unusable_state_and_large_sr_before_inference(tmp_path):
    from pipe_inspector.vision.restoration_candidates import make_restoration_model

    path = tmp_path / "wrong.pth"
    torch.save({"params": {}}, path)
    model = make_restoration_model("hat-l-x4-imagenet", path, "cpu")
    with pytest.raises(ValueError, match="params_ema"):
        model.enhance_image(Image.new("RGB", (16, 16)))
    with pytest.raises(ValueError, match="output.*limit"):
        model.enhance_image(Image.new("RGB", (600, 600)))


def test_baseline_factory_reuses_existing_adapter(tmp_path):
    from pipe_inspector.vision.enhancement_models import LocalEnhancementModel
    from pipe_inspector.vision.restoration_candidates import make_restoration_model

    model = make_restoration_model("restormer-motion-deblurring", tmp_path / "existing", "cpu")
    assert isinstance(model, LocalEnhancementModel)
    assert model.kind == "deblur"


def test_convir_selects_full_resolution_head_without_normalization(monkeypatch, tmp_path):
    from pipe_inspector.vision.restoration_candidates import make_restoration_model

    class Heads(nn.Module):
        def forward(self, x):
            assert x.shape[-2:] == (288, 320)
            return [x[..., ::4, ::4] * 0, x[..., ::2, ::2] * 0, x]

    model = make_restoration_model("convir-l-haze4k", tmp_path / "unused", "cpu")
    monkeypatch.setattr(model, "_ensure_model", lambda: Heads())
    original = Image.new("RGB", (313, 257), (17, 98, 201))
    output = model.enhance_image(original)
    assert output.size == original.size
    assert output.tobytes() == original.tobytes()


def test_convir_dynamic_kernel_preserves_feature_shape():
    from pipe_inspector.vision._enhancement_vendor.convir import MultiShapeKernel

    model = MultiShapeKernel(8, dilation=1).eval()
    with torch.inference_mode():
        output = model(torch.ones(1, 8, 12, 16))
    assert output.shape == (1, 8, 12, 16)
    assert torch.isfinite(output).all()


def test_candidate_checkpoint_strict_loading_and_unload(monkeypatch, tmp_path):
    from pipe_inspector.vision.restoration_candidates import make_restoration_model

    module = importlib.import_module("pipe_inspector.vision.restoration_candidates")
    path = tmp_path / "weights.pth"
    torch.save({"params_ema": {"weight": torch.ones(3, 3, 1, 1), "bias": torch.zeros(3)}}, path)
    monkeypatch.setattr(module, "_build_candidate", lambda model_id: nn.Conv2d(3, 3, 1))
    model = make_restoration_model("hat-l-x4-imagenet", path, "cpu")
    loaded = model._ensure_model()
    assert not loaded.training
    assert loaded.weight.detach().tolist() == [[[[1.0]], [[1.0]], [[1.0]]]] * 3
    assert model._ensure_model() is loaded
    model.unload()
    assert model._model is None
    assert model._ensure_model() is not loaded
    model.unload()
    torch.save({"params_ema": {"wrong": torch.zeros(1)}}, path)
    with pytest.raises(RuntimeError, match="Missing key"):
        model._ensure_model()
