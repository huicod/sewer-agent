"""Concertormer architecture for RealBlur-J deblurring.

Source: https://github.com/setsunil/Concertormer/blob/
        94269c556277805051644343e3a2cc76c4cd7aaf/
        basicsr/models/archs/Concertormer_arch.py
License: MIT for the architecture; the repository's bundled license also carries the
Apache-2.0 notice for the BasicSR-derived ``LayerNorm2d`` implementation.

This inference-only adaptation removes the BasicSR dependency, uses legal replicate
padding for inputs too small for reflection. Parameter names, model math, and the list
of intermediate supervision tensors returned by ``forward`` remain checkpoint-compatible.
``build_concertormer`` pins the official RealBlur-J test configuration.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import torch
import torch.nn.functional as F
from einops import rearrange
from torch import nn


class _LayerNormFunction(torch.autograd.Function):
    @staticmethod
    def forward(ctx, x, weight, bias, eps):
        ctx.eps = eps
        _, channels, _, _ = x.size()
        mean = x.mean(1, keepdim=True)
        variance = (x - mean).pow(2).mean(1, keepdim=True)
        normalized = (x - mean) / (variance + eps).sqrt()
        ctx.save_for_backward(normalized, variance, weight)
        return weight.view(1, channels, 1, 1) * normalized + bias.view(
            1, channels, 1, 1
        )

    @staticmethod
    def backward(ctx, grad_output):
        normalized, variance, weight = ctx.saved_tensors
        gradient = grad_output * weight.view(1, -1, 1, 1)
        mean_gradient = gradient.mean(dim=1, keepdim=True)
        mean_gradient_normalized = (gradient * normalized).mean(dim=1, keepdim=True)
        grad_input = 1.0 / torch.sqrt(variance + ctx.eps) * (
            gradient - normalized * mean_gradient_normalized - mean_gradient
        )
        return (
            grad_input,
            (grad_output * normalized).sum(dim=(0, 2, 3)),
            grad_output.sum(dim=(0, 2, 3)),
            None,
        )


class LayerNorm2d(nn.Module):
    def __init__(self, channels: int, eps: float = 1e-6) -> None:
        super().__init__()
        self.weight = nn.Parameter(torch.ones(channels))
        self.bias = nn.Parameter(torch.zeros(channels))
        self.eps = eps

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return _LayerNormFunction.apply(x, self.weight, self.bias, self.eps)


def _window_partition(x: torch.Tensor, window_size: int) -> torch.Tensor:
    return rearrange(
        x,
        "b (h kh) (w kw) c -> (b h w c) (kh kw)",
        kh=window_size,
        kw=window_size,
    )


class CSAttention(nn.Module):
    def __init__(
        self,
        dim: int,
        num_heads: int,
        ffn_expansion_factor: float,
        bias: bool,
        shift: bool,
    ) -> None:
        super().__init__()
        hidden_dim = int(ffn_expansion_factor * dim)
        if hidden_dim % (num_heads * 4):
            raise ValueError("Concertormer hidden dimension must divide into four head groups")
        self.dim = dim
        self.h_dim = hidden_dim
        self.shift = shift
        self.num_heads = num_heads
        self.ffn_expansion_factor = ffn_expansion_factor
        self.ks = 8
        self.bias = bias
        self.temperature = nn.Parameter(torch.ones(4, num_heads, 1, 1, 1))
        self.r_talking = nn.Parameter(
            torch.empty(
                num_heads,
                hidden_dim // num_heads // 4,
                hidden_dim // num_heads // 4,
                num_heads,
            )
        )
        self.g_talking = nn.Parameter(
            torch.empty(num_heads, self.ks**2, self.ks**2, num_heads)
        )
        nn.init.kaiming_normal_(self.r_talking, a=math.sqrt(5))
        nn.init.kaiming_normal_(self.g_talking, a=math.sqrt(5))
        self.b_talking = nn.Conv3d(
            num_heads, num_heads, (3, 3, 1), padding="same", bias=bias
        )
        self.l_talking = nn.Conv3d(
            num_heads, num_heads, (3, 3, 1), padding="same", bias=bias
        )
        self.shift_size = self.ks // 2
        self.to_quz = nn.Conv2d(dim, hidden_dim * 3, kernel_size=1, bias=bias)
        self.to_kv = nn.Conv2d(dim, hidden_dim * 2, kernel_size=1, bias=bias)
        self.dw_u = nn.Conv2d(
            hidden_dim, hidden_dim, 3, padding=1, groups=hidden_dim, bias=bias
        )
        self.project = nn.Conv2d(hidden_dim, hidden_dim, kernel_size=1, bias=bias)
        self.project_out = nn.Conv2d(hidden_dim, dim, kernel_size=1, bias=bias)
        self.sca = nn.Sequential(
            nn.AdaptiveAvgPool2d(1),
            nn.Conv2d(hidden_dim, hidden_dim, kernel_size=1, bias=True),
        )

    def _attention(
        self,
        q: torch.Tensor,
        k: torch.Tensor,
        v: torch.Tensor,
    ) -> torch.Tensor:
        batch, _, height, width = q.shape
        if self.shift:
            q, k, v = (
                torch.roll(item, shifts=(-self.shift_size, -self.shift_size), dims=(-1, -2))
                for item in (q, k, v)
            )
        q, k, v = (
            rearrange(
                item,
                "b (head c) (h kh) (w kw) -> b head (h w) (kh kw) c",
                head=self.num_heads,
                kh=self.ks,
                kw=self.ks,
            )
            for item in (q, k, v)
        )
        qb, ql, qg, qr = q.chunk(4, dim=-1)
        kb, kl, kg, kr = k.chunk(4, dim=-1)
        vb, vl, vg, vr = v.chunk(4, dim=-1)
        qb, kb = (F.normalize(item, dim=-1) for item in (qb, kb))
        ql, kl = (F.normalize(item, dim=-2) for item in (ql, kl))
        qr, qg, kr, kg = (F.normalize(item, dim=-3) for item in (qr, qg, kr, kg))

        attention_b = qb @ kb.permute(0, 1, 2, 4, 3) * self.temperature[0]
        attention_l = ql.permute(0, 1, 2, 4, 3) @ kl * self.temperature[1]
        attention_g = (
            qg.permute(0, 1, 3, 4, 2) @ kg.permute(0, 1, 3, 2, 4)
        ) * self.temperature[2]
        attention_r = (
            qr.permute(0, 1, 4, 3, 2) @ kr.permute(0, 1, 4, 2, 3)
        ) * self.temperature[3]

        attention_b = rearrange(
            self.b_talking(
                rearrange(
                    attention_b,
                    "b head (h w) k1 k2 -> b head h w (k1 k2)",
                    h=height // self.ks,
                )
            ),
            "b head h w (k1 k2) -> b head (h w) k1 k2",
            k1=self.ks**2,
        )
        group_channels = self.h_dim // self.num_heads // 4
        attention_l = rearrange(
            self.l_talking(
                rearrange(
                    attention_l,
                    "b head (h w) c1 c2 -> b head h w (c1 c2)",
                    h=height // self.ks,
                )
            ),
            "b head h w (c1 c2) -> b head (h w) c1 c2",
            c1=group_channels,
        )
        attention_g = torch.einsum("hklt, bhk... -> btl...", self.g_talking, attention_g)
        attention_r = torch.einsum("hcdt, bhc... -> btd...", self.r_talking, attention_r)
        if self.shift:
            attention_b = attention_b + self._calculate_mask(
                height, width, device=q.device, dtype=q.dtype
            )[None, None, ...]
        attention_b, attention_l, attention_g, attention_r = (
            torch.softmax(item, dim=-1)
            for item in (attention_b, attention_l, attention_g, attention_r)
        )
        output_b = rearrange(
            attention_b @ vb,
            "b head (h w) (kh kw) c -> b (head c) (h kh) (w kw)",
            b=batch,
            h=height // self.ks,
            kh=self.ks,
        )
        output_l = rearrange(
            attention_l @ vl.transpose(-1, -2),
            "b head (h w) c (kh kw) -> b (head c) (h kh) (w kw)",
            b=batch,
            h=height // self.ks,
            kh=self.ks,
        )
        output_g = rearrange(
            attention_g @ vg.permute(0, 1, 3, 4, 2),
            "b head (kh kw) c (h w) -> b (head c) (h kh) (w kw)",
            b=batch,
            h=height // self.ks,
            kh=self.ks,
        )
        output_r = rearrange(
            attention_r @ vr.transpose(-1, -3),
            "b head c (kh kw) (h w) -> b (head c) (h kh) (w kw)",
            b=batch,
            h=height // self.ks,
            kh=self.ks,
        )
        output = torch.cat((output_b, output_l, output_g, output_r), dim=1)
        if self.shift:
            output = torch.roll(
                output, shifts=(self.shift_size, self.shift_size), dims=(-1, -2)
            )
        return output

    def forward(self, x: torch.Tensor, y: torch.Tensor | None = None) -> torch.Tensor:
        k, v = self.to_kv(x if y is None else y).chunk(2, dim=1)
        q, u, z = self.to_quz(x).chunk(3, dim=1)
        output = self._attention(q, k, v)
        output = self.project(output * self.sca(output)) + self.dw_u(u)
        return self.project_out(output * z)

    def _calculate_mask(
        self,
        height: int,
        width: int,
        *,
        device: torch.device,
        dtype: torch.dtype,
    ) -> torch.Tensor:
        image_mask = torch.zeros((1, height, width, 1), device=device, dtype=dtype)
        height_slices = (
            slice(0, -self.ks),
            slice(-self.ks, -self.shift_size),
            slice(-self.shift_size, None),
        )
        width_slices = height_slices
        count = 0
        for height_slice in height_slices:
            for width_slice in width_slices:
                image_mask[:, height_slice, width_slice, :] = count
                count += 1
        windows = _window_partition(image_mask, self.ks)
        mask = windows.unsqueeze(1) - windows.unsqueeze(2)
        return mask.masked_fill(mask != 0, -100.0).masked_fill(mask == 0, 0.0)


class LocalCSAttention(CSAttention):
    def __init__(self, *args, kernel_size: tuple[int, int], **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.kernel_size = kernel_size

    def _grids(
        self, x: torch.Tensor
    ) -> tuple[torch.Tensor, list[tuple[int, int]], tuple[int, int, int, int]]:
        batch, channels, height, width = x.shape
        if batch != 1:
            raise ValueError("ConcertormerLocal inference supports batch size one")
        kernel_height = min(height, self.kernel_size[0])
        kernel_width = min(width, self.kernel_size[1])
        rows = (height - 1) // kernel_height + 1
        columns = (width - 1) // kernel_width + 1
        step_y = (
            kernel_height
            if rows == 1
            else math.ceil((height - kernel_height) / (rows - 1) - 1e-8)
        )
        step_x = (
            kernel_width
            if columns == 1
            else math.ceil((width - kernel_width) / (columns - 1) - 1e-8)
        )
        parts: list[torch.Tensor] = []
        locations: list[tuple[int, int]] = []
        top = 0
        last_row = False
        while top < height and not last_row:
            if top + kernel_height >= height:
                top = height - kernel_height
                last_row = True
            left = 0
            last_column = False
            while left < width and not last_column:
                if left + kernel_width >= width:
                    left = width - kernel_width
                    last_column = True
                parts.append(x[..., top : top + kernel_height, left : left + kernel_width])
                locations.append((top, left))
                left += step_x
            top += step_y
        return torch.cat(parts), locations, (batch, channels, height, width)

    def _grids_inverse(
        self,
        outputs: torch.Tensor,
        locations: list[tuple[int, int]],
        original_size: tuple[int, int, int, int],
    ) -> torch.Tensor:
        batch, channels, height, width = original_size
        restored = outputs.new_zeros(original_size)
        counts = outputs.new_zeros((batch, 1, height, width))
        kernel_height = min(height, self.kernel_size[0])
        kernel_width = min(width, self.kernel_size[1])
        for index, (top, left) in enumerate(locations):
            restored[..., top : top + kernel_height, left : left + kernel_width] += outputs[
                index
            ]
            counts[..., top : top + kernel_height, left : left + kernel_width] += 1
        return restored / counts

    def forward(self, x: torch.Tensor, y: torch.Tensor | None = None) -> torch.Tensor:
        q, u, z = self.to_quz(x).chunk(3, dim=1)
        k, v = self.to_kv(x if y is None else y).chunk(2, dim=1)
        q_parts, locations, original_size = self._grids(q)
        k_parts, k_locations, k_size = self._grids(k)
        v_parts, v_locations, v_size = self._grids(v)
        if k_locations != locations or v_locations != locations or k_size != v_size:
            raise RuntimeError("Concertormer local attention grid mismatch")
        output = self._attention(q_parts, k_parts, v_parts)
        output = self._grids_inverse(output * self.sca(output), locations, original_size)
        output = self.project(output) + self.dw_u(u)
        return self.project_out(z * output)


class CSABlock(nn.Module):
    def __init__(
        self,
        dim: int,
        head: int,
        ffn_expansion_factor: float,
        index: int,
        *,
        cross: bool = False,
    ) -> None:
        super().__init__()
        self.cross = cross
        if cross:
            self.norm1 = LayerNorm2d(dim)
            self.norm2 = LayerNorm2d(dim)
        else:
            self.norm = LayerNorm2d(dim)
        self.m = CSAttention(
            dim,
            num_heads=head,
            ffn_expansion_factor=ffn_expansion_factor,
            bias=False,
            shift=bool(index % 2),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if self.cross:
            x, y = x.chunk(2, dim=1)
            return x + self.m(self.norm1(x), self.norm2(y))
        return x + self.m(self.norm(x))


class Concertormer(nn.Module):
    def __init__(
        self,
        img_channel: int = 3,
        width: int = 16,
        middle_blk_num: int = 1,
        ffn_expansion_factor: float = 1,
        enc_blk_nums: Sequence[int] = (),
        dec_blk_nums: Sequence[int] = (),
        enc_heads: Sequence[int] = (),
        middle_heads: int = 1,
        dec_heads: Sequence[int] = (),
    ) -> None:
        super().__init__()
        if not (
            len(enc_blk_nums)
            == len(dec_blk_nums)
            == len(enc_heads)
            == len(dec_heads)
        ):
            raise ValueError("Concertormer encoder/decoder configurations must have equal lengths")
        self.intro = nn.Conv2d(img_channel, width, 3, padding=1, bias=True)
        self.ending = nn.Conv2d(width, img_channel, 3, padding=1, bias=True)
        self.intros = nn.ModuleList()
        self.endings = nn.ModuleList()
        self.encoders = nn.ModuleList()
        self.decoders = nn.ModuleList()
        self.ups = nn.ModuleList()
        self.downs = nn.ModuleList()
        self.n_layer = len(enc_blk_nums)
        for index in range(self.n_layer):
            self.intros.append(
                nn.Conv2d(img_channel, width * 2 ** (index + 1), 3, padding=1, bias=True)
            )
            self.endings.append(
                nn.Conv2d(
                    width * 2 ** (self.n_layer - index),
                    img_channel,
                    3,
                    padding=1,
                    bias=True,
                )
            )
        channels = width
        for blocks, heads in zip(enc_blk_nums, enc_heads, strict=True):
            self.encoders.append(
                nn.Sequential(
                    *(
                        CSABlock(
                            channels,
                            heads,
                            ffn_expansion_factor,
                            index,
                            cross=index == 0 and len(self.encoders) > 0,
                        )
                        for index in range(blocks)
                    )
                )
            )
            self.downs.append(nn.Conv2d(channels, 2 * channels, 2, 2))
            channels *= 2
        self.middle_blks = nn.Sequential(
            *(
                CSABlock(
                    channels,
                    middle_heads,
                    ffn_expansion_factor,
                    index,
                    cross=index == 0,
                )
                for index in range(middle_blk_num)
            )
        )
        for blocks, heads in zip(dec_blk_nums, dec_heads, strict=True):
            self.ups.append(
                nn.Sequential(
                    nn.Conv2d(channels, channels * 2, 1, bias=False),
                    nn.PixelShuffle(2),
                )
            )
            channels //= 2
            self.decoders.append(
                nn.Sequential(
                    *(
                        CSABlock(
                            channels,
                            heads,
                            ffn_expansion_factor,
                            index,
                            cross=index == 0,
                        )
                        for index in range(blocks)
                    )
                )
            )
        self.padder_size = 2 ** (len(self.encoders) + 3)

    def forward(self, input_tensor: torch.Tensor) -> list[torch.Tensor]:
        height, width = input_tensor.shape[-2:]
        padded = self.check_image_size(input_tensor)
        x = self.intro(padded)
        scaled_inputs = [
            F.interpolate(padded, scale_factor=0.5 ** (index + 1), mode="bilinear")
            for index in range(self.n_layer)
        ]
        encoder_outputs: list[torch.Tensor] = []
        for encoder, down, intro, scaled_input in zip(
            self.encoders, self.downs, self.intros, scaled_inputs, strict=True
        ):
            x = encoder(x)
            encoder_outputs.append(x)
            x = torch.cat((down(x), intro(scaled_input)), dim=1)
        x = self.middle_blks(x)
        decoder_outputs: list[torch.Tensor] = []
        for decoder, up, encoder_skip, ending, scaled_input in zip(
            self.decoders,
            self.ups,
            reversed(encoder_outputs),
            self.endings,
            reversed(scaled_inputs),
            strict=True,
        ):
            decoder_outputs.append(ending(x) + scaled_input)
            x = decoder(torch.cat((up(x), encoder_skip), dim=1))
        output = self.ending(x) + padded
        decoder_outputs.append(output[..., :height, :width])
        return decoder_outputs

    def check_image_size(self, x: torch.Tensor) -> torch.Tensor:
        height, width = x.shape[-2:]
        pad_height = (self.padder_size - height % self.padder_size) % self.padder_size
        pad_width = (self.padder_size - width % self.padder_size) % self.padder_size
        if not pad_height and not pad_width:
            return x
        mode = "reflect" if height > pad_height and width > pad_width else "replicate"
        return F.pad(x, (0, pad_width, 0, pad_height), mode=mode)


class ConcertormerLocal(Concertormer):
    def __init__(
        self,
        *args,
        train_size: tuple[int, int, int, int] = (1, 3, 256, 256),
        grid_factor: float = 2.0,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        base_height = int(train_size[-2] * grid_factor)
        base_width = int(train_size[-1] * grid_factor)
        for stage, encoder in enumerate(self.encoders):
            self._replace_stage(encoder, (base_height // 2**stage, base_width // 2**stage))
        self._replace_stage(
            self.middle_blks,
            (base_height // 2**self.n_layer, base_width // 2**self.n_layer),
        )
        for stage, decoder in enumerate(self.decoders):
            scale = self.n_layer - stage - 1
            self._replace_stage(decoder, (base_height // 2**scale, base_width // 2**scale))

    @staticmethod
    def _replace_stage(stage: nn.Sequential, kernel_size: tuple[int, int]) -> None:
        for block in stage:
            attention = block.m
            replacement = LocalCSAttention(
                dim=attention.dim,
                num_heads=attention.num_heads,
                ffn_expansion_factor=attention.ffn_expansion_factor,
                bias=attention.bias,
                shift=attention.shift,
                kernel_size=kernel_size,
            )
            block.m = replacement


def build_concertormer() -> ConcertormerLocal:
    """Build the architecture pinned by the official RealBlur-J test config."""
    return ConcertormerLocal(
        width=48,
        ffn_expansion_factor=2,
        train_size=(1, 3, 256, 256),
        grid_factor=1.25,
        enc_blk_nums=(6, 8, 24),
        middle_blk_num=2,
        dec_blk_nums=(24, 8, 8),
        enc_heads=(1, 2, 4),
        middle_heads=8,
        dec_heads=(4, 2, 1),
    )


__all__ = ["Concertormer", "ConcertormerLocal", "build_concertormer"]
