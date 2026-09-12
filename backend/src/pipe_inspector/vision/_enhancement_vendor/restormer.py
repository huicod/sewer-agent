"""Restormer architecture for motion deblurring.

Source: https://github.com/swz30/Restormer/blob/
        68dc6ac472db26f16361150cb7a96a1bc87da93f/basicsr/models/archs/restormer_arch.py
License: MIT, copyright (c) 2022 Syed Waqas Zamir and contributors.

Only formatting, annotations, and unused debugger-import removal differ from upstream.
"""

from __future__ import annotations

import numbers
from collections.abc import Sequence

import torch
import torch.nn.functional as F
from einops import rearrange
from torch import nn


def to_3d(x: torch.Tensor) -> torch.Tensor:
    return rearrange(x, "b c h w -> b (h w) c")


def to_4d(x: torch.Tensor, height: int, width: int) -> torch.Tensor:
    return rearrange(x, "b (h w) c -> b c h w", h=height, w=width)


class BiasFreeLayerNorm(nn.Module):
    def __init__(self, normalized_shape: int | Sequence[int]) -> None:
        super().__init__()
        if isinstance(normalized_shape, numbers.Integral):
            normalized_shape = (int(normalized_shape),)
        normalized_shape = torch.Size(normalized_shape)
        if len(normalized_shape) != 1:
            raise ValueError("Restormer layer normalization expects one dimension")
        self.weight = nn.Parameter(torch.ones(normalized_shape))
        self.normalized_shape = normalized_shape

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        sigma = x.var(-1, keepdim=True, unbiased=False)
        return x / torch.sqrt(sigma + 1e-5) * self.weight


class WithBiasLayerNorm(nn.Module):
    def __init__(self, normalized_shape: int | Sequence[int]) -> None:
        super().__init__()
        if isinstance(normalized_shape, numbers.Integral):
            normalized_shape = (int(normalized_shape),)
        normalized_shape = torch.Size(normalized_shape)
        if len(normalized_shape) != 1:
            raise ValueError("Restormer layer normalization expects one dimension")
        self.weight = nn.Parameter(torch.ones(normalized_shape))
        self.bias = nn.Parameter(torch.zeros(normalized_shape))
        self.normalized_shape = normalized_shape

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        mean = x.mean(-1, keepdim=True)
        sigma = x.var(-1, keepdim=True, unbiased=False)
        return (x - mean) / torch.sqrt(sigma + 1e-5) * self.weight + self.bias


class LayerNorm(nn.Module):
    def __init__(self, dim: int, layer_norm_type: str) -> None:
        super().__init__()
        if layer_norm_type == "BiasFree":
            self.body = BiasFreeLayerNorm(dim)
        else:
            self.body = WithBiasLayerNorm(dim)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        height, width = x.shape[-2:]
        return to_4d(self.body(to_3d(x)), height, width)


class FeedForward(nn.Module):
    def __init__(self, dim: int, ffn_expansion_factor: float, bias: bool) -> None:
        super().__init__()
        hidden_features = int(dim * ffn_expansion_factor)
        self.project_in = nn.Conv2d(dim, hidden_features * 2, kernel_size=1, bias=bias)
        self.dwconv = nn.Conv2d(
            hidden_features * 2,
            hidden_features * 2,
            kernel_size=3,
            stride=1,
            padding=1,
            groups=hidden_features * 2,
            bias=bias,
        )
        self.project_out = nn.Conv2d(hidden_features, dim, kernel_size=1, bias=bias)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.project_in(x)
        x1, x2 = self.dwconv(x).chunk(2, dim=1)
        return self.project_out(F.gelu(x1) * x2)


class Attention(nn.Module):
    def __init__(self, dim: int, num_heads: int, bias: bool) -> None:
        super().__init__()
        self.num_heads = num_heads
        self.temperature = nn.Parameter(torch.ones(num_heads, 1, 1))
        self.qkv = nn.Conv2d(dim, dim * 3, kernel_size=1, bias=bias)
        self.qkv_dwconv = nn.Conv2d(
            dim * 3,
            dim * 3,
            kernel_size=3,
            stride=1,
            padding=1,
            groups=dim * 3,
            bias=bias,
        )
        self.project_out = nn.Conv2d(dim, dim, kernel_size=1, bias=bias)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        _, _, height, width = x.shape
        q, k, v = self.qkv_dwconv(self.qkv(x)).chunk(3, dim=1)
        q = rearrange(q, "b (head c) h w -> b head c (h w)", head=self.num_heads)
        k = rearrange(k, "b (head c) h w -> b head c (h w)", head=self.num_heads)
        v = rearrange(v, "b (head c) h w -> b head c (h w)", head=self.num_heads)
        q = F.normalize(q, dim=-1)
        k = F.normalize(k, dim=-1)
        attention = (q @ k.transpose(-2, -1)) * self.temperature
        output = attention.softmax(dim=-1) @ v
        output = rearrange(
            output,
            "b head c (h w) -> b (head c) h w",
            head=self.num_heads,
            h=height,
            w=width,
        )
        return self.project_out(output)


class TransformerBlock(nn.Module):
    def __init__(
        self,
        dim: int,
        num_heads: int,
        ffn_expansion_factor: float,
        bias: bool,
        layer_norm_type: str,
    ) -> None:
        super().__init__()
        self.norm1 = LayerNorm(dim, layer_norm_type)
        self.attn = Attention(dim, num_heads, bias)
        self.norm2 = LayerNorm(dim, layer_norm_type)
        self.ffn = FeedForward(dim, ffn_expansion_factor, bias)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = x + self.attn(self.norm1(x))
        return x + self.ffn(self.norm2(x))


class OverlapPatchEmbed(nn.Module):
    def __init__(self, in_channels: int = 3, embed_dim: int = 48, bias: bool = False) -> None:
        super().__init__()
        self.proj = nn.Conv2d(
            in_channels, embed_dim, kernel_size=3, stride=1, padding=1, bias=bias
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.proj(x)


class Downsample(nn.Module):
    def __init__(self, features: int) -> None:
        super().__init__()
        self.body = nn.Sequential(
            nn.Conv2d(features, features // 2, kernel_size=3, stride=1, padding=1, bias=False),
            nn.PixelUnshuffle(2),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.body(x)


class Upsample(nn.Module):
    def __init__(self, features: int) -> None:
        super().__init__()
        self.body = nn.Sequential(
            nn.Conv2d(features, features * 2, kernel_size=3, stride=1, padding=1, bias=False),
            nn.PixelShuffle(2),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.body(x)


def _blocks(
    count: int,
    *,
    dim: int,
    heads: int,
    ffn_expansion_factor: float,
    bias: bool,
    layer_norm_type: str,
) -> nn.Sequential:
    return nn.Sequential(
        *[
            TransformerBlock(
                dim=dim,
                num_heads=heads,
                ffn_expansion_factor=ffn_expansion_factor,
                bias=bias,
                layer_norm_type=layer_norm_type,
            )
            for _ in range(count)
        ]
    )


class Restormer(nn.Module):
    def __init__(
        self,
        inp_channels: int = 3,
        out_channels: int = 3,
        dim: int = 48,
        num_blocks: Sequence[int] = (4, 6, 6, 8),
        num_refinement_blocks: int = 4,
        heads: Sequence[int] = (1, 2, 4, 8),
        ffn_expansion_factor: float = 2.66,
        bias: bool = False,
        layer_norm_type: str = "WithBias",
        dual_pixel_task: bool = False,
    ) -> None:
        super().__init__()
        block_args = {
            "ffn_expansion_factor": ffn_expansion_factor,
            "bias": bias,
            "layer_norm_type": layer_norm_type,
        }
        self.patch_embed = OverlapPatchEmbed(inp_channels, dim)
        self.encoder_level1 = _blocks(
            num_blocks[0], dim=dim, heads=heads[0], **block_args
        )
        self.down1_2 = Downsample(dim)
        self.encoder_level2 = _blocks(
            num_blocks[1], dim=dim * 2, heads=heads[1], **block_args
        )
        self.down2_3 = Downsample(dim * 2)
        self.encoder_level3 = _blocks(
            num_blocks[2], dim=dim * 4, heads=heads[2], **block_args
        )
        self.down3_4 = Downsample(dim * 4)
        self.latent = _blocks(num_blocks[3], dim=dim * 8, heads=heads[3], **block_args)

        self.up4_3 = Upsample(dim * 8)
        self.reduce_chan_level3 = nn.Conv2d(dim * 8, dim * 4, kernel_size=1, bias=bias)
        self.decoder_level3 = _blocks(
            num_blocks[2], dim=dim * 4, heads=heads[2], **block_args
        )
        self.up3_2 = Upsample(dim * 4)
        self.reduce_chan_level2 = nn.Conv2d(dim * 4, dim * 2, kernel_size=1, bias=bias)
        self.decoder_level2 = _blocks(
            num_blocks[1], dim=dim * 2, heads=heads[1], **block_args
        )
        self.up2_1 = Upsample(dim * 2)
        self.decoder_level1 = _blocks(
            num_blocks[0], dim=dim * 2, heads=heads[0], **block_args
        )
        self.refinement = _blocks(
            num_refinement_blocks, dim=dim * 2, heads=heads[0], **block_args
        )
        self.dual_pixel_task = dual_pixel_task
        if dual_pixel_task:
            self.skip_conv = nn.Conv2d(dim, dim * 2, kernel_size=1, bias=bias)
        self.output = nn.Conv2d(
            dim * 2, out_channels, kernel_size=3, stride=1, padding=1, bias=bias
        )

    def forward(self, inp_img: torch.Tensor) -> torch.Tensor:
        inp_enc_level1 = self.patch_embed(inp_img)
        out_enc_level1 = self.encoder_level1(inp_enc_level1)
        inp_enc_level2 = self.down1_2(out_enc_level1)
        out_enc_level2 = self.encoder_level2(inp_enc_level2)
        inp_enc_level3 = self.down2_3(out_enc_level2)
        out_enc_level3 = self.encoder_level3(inp_enc_level3)
        inp_enc_level4 = self.down3_4(out_enc_level3)
        latent = self.latent(inp_enc_level4)

        inp_dec_level3 = self.up4_3(latent)
        inp_dec_level3 = torch.cat([inp_dec_level3, out_enc_level3], 1)
        out_dec_level3 = self.decoder_level3(self.reduce_chan_level3(inp_dec_level3))
        inp_dec_level2 = self.up3_2(out_dec_level3)
        inp_dec_level2 = torch.cat([inp_dec_level2, out_enc_level2], 1)
        out_dec_level2 = self.decoder_level2(self.reduce_chan_level2(inp_dec_level2))
        inp_dec_level1 = self.up2_1(out_dec_level2)
        inp_dec_level1 = torch.cat([inp_dec_level1, out_enc_level1], 1)
        out_dec_level1 = self.refinement(self.decoder_level1(inp_dec_level1))

        if self.dual_pixel_task:
            out_dec_level1 = out_dec_level1 + self.skip_conv(inp_enc_level1)
            return self.output(out_dec_level1)
        return self.output(out_dec_level1) + inp_img
