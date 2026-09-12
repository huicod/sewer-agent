from __future__ import annotations

from pathlib import Path

import pytest
import torch
from pipe_inspector.vision._enhancement_vendor.concertormer import (
    Concertormer,
    ConcertormerLocal,
    build_concertormer,
)

CHECKPOINT = Path("var/models/enhancement/concertormer-realblur-j.pth")


def test_concertormer_preserves_odd_rgb_size_and_unit_range_residual() -> None:
    model = Concertormer(
        width=8,
        middle_blk_num=1,
        ffn_expansion_factor=2,
        enc_blk_nums=(1,),
        dec_blk_nums=(1,),
        enc_heads=(1,),
        middle_heads=1,
        dec_heads=(1,),
    ).eval()
    with torch.no_grad():
        for parameter in model.parameters():
            parameter.zero_()
    image = torch.rand((1, 3, 17, 13), dtype=torch.float32)

    with torch.inference_mode():
        outputs = model(image)

    assert len(outputs) == 2
    assert outputs[-1].shape == image.shape
    torch.testing.assert_close(outputs[-1], image)


def test_concertormer_tiny_input_uses_legal_padding_and_returns_finite_rgb() -> None:
    torch.manual_seed(7)
    model = Concertormer(
        width=8,
        middle_blk_num=1,
        ffn_expansion_factor=2,
        enc_blk_nums=(1,),
        dec_blk_nums=(1,),
        enc_heads=(1,),
        middle_heads=1,
        dec_heads=(1,),
    ).eval()
    image = torch.rand((1, 3, 1, 2), dtype=torch.float32)

    with torch.inference_mode():
        outputs = model(image)

    assert len(outputs) == 2
    assert outputs[-1].shape == image.shape
    assert torch.isfinite(outputs[-1]).all().item()


def test_realblur_j_builder_matches_official_architecture() -> None:
    model = build_concertormer()

    assert isinstance(model, ConcertormerLocal)
    assert model.intro.weight.shape == (48, 3, 3, 3)
    assert [len(stage) for stage in model.encoders] == [6, 8, 24]
    assert len(model.middle_blks) == 2
    assert [len(stage) for stage in model.decoders] == [24, 8, 8]
    assert sum(parameter.numel() for parameter in model.parameters()) == 50_535_664


def test_official_realblur_j_params_load_strictly() -> None:
    if not CHECKPOINT.is_file():
        pytest.skip("official RealBlur-J checkpoint is provisioned separately")

    payload = torch.load(CHECKPOINT, map_location="cpu", weights_only=True)
    assert isinstance(payload, dict)
    assert isinstance(payload.get("params"), dict)
    model = build_concertormer()

    incompatible = model.load_state_dict(payload["params"], strict=True)

    assert incompatible.missing_keys == []
    assert incompatible.unexpected_keys == []
