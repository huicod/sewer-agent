"""HVI-CIDNet inference architecture.

Source: https://github.com/Fediory/HVI-CIDNet/tree/
        eb43d7d91e9a336c66856824ff9e4603ae41f408
License: MIT, copyright (c) 2024 Fediory Feng.

This combines the official ``net`` modules without the optional Hugging Face
mixin. Inference math and state-dict names are retained. Padding/cropping from
the authors' ``eval_hf.py`` is kept inside ``forward`` so callers always receive
the original spatial size.
"""

from __future__ import annotations

import math

import torch
import torch.nn.functional as F
from einops import rearrange
from torch import nn


class _LayerNorm(nn.Module):
    def __init__(self, normalized_shape: int, eps: float = 1e-6) -> None:
        super().__init__()
        self.weight = nn.Parameter(torch.ones(normalized_shape))
        self.bias = nn.Parameter(torch.zeros(normalized_shape))
        self.eps = eps

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        mean = x.mean(1, keepdim=True)
        variance = (x - mean).pow(2).mean(1, keepdim=True)
        normalized = (x - mean) / torch.sqrt(variance + self.eps)
        return self.weight[:, None, None] * normalized + self.bias[:, None, None]


class NormDownsample(nn.Module):
    def __init__(self, in_ch: int, out_ch: int, scale: float = 0.5, use_norm: bool = False):
        super().__init__()
        self.use_norm = use_norm
        if use_norm:
            self.norm = _LayerNorm(out_ch)
        self.prelu = nn.PReLU()
        self.down = nn.Sequential(
            nn.Conv2d(in_ch, out_ch, 3, padding=1, bias=False),
            nn.UpsamplingBilinear2d(scale_factor=scale),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.prelu(self.down(x))
        return self.norm(x) if self.use_norm else x


class NormUpsample(nn.Module):
    def __init__(self, in_ch: int, out_ch: int, scale: int = 2, use_norm: bool = False):
        super().__init__()
        self.use_norm = use_norm
        if use_norm:
            self.norm = _LayerNorm(out_ch)
        self.prelu = nn.PReLU()
        self.up_scale = nn.Sequential(
            nn.Conv2d(in_ch, out_ch, 3, padding=1, bias=False),
            nn.UpsamplingBilinear2d(scale_factor=scale),
        )
        self.up = nn.Conv2d(out_ch * 2, out_ch, 1, bias=False)

    def forward(self, x: torch.Tensor, skip: torch.Tensor) -> torch.Tensor:
        x = self.prelu(self.up(torch.cat([self.up_scale(x), skip], dim=1)))
        return self.norm(x) if self.use_norm else x


class CAB(nn.Module):
    def __init__(self, dim: int, num_heads: int, bias: bool) -> None:
        super().__init__()
        self.num_heads = num_heads
        self.temperature = nn.Parameter(torch.ones(num_heads, 1, 1))
        self.q = nn.Conv2d(dim, dim, 1, bias=bias)
        self.q_dwconv = nn.Conv2d(dim, dim, 3, padding=1, groups=dim, bias=bias)
        self.kv = nn.Conv2d(dim, dim * 2, 1, bias=bias)
        self.kv_dwconv = nn.Conv2d(dim * 2, dim * 2, 3, padding=1, groups=dim * 2, bias=bias)
        self.project_out = nn.Conv2d(dim, dim, 1, bias=bias)

    def forward(self, x: torch.Tensor, y: torch.Tensor) -> torch.Tensor:
        height, width = x.shape[-2:]
        query = self.q_dwconv(self.q(x))
        key, value = self.kv_dwconv(self.kv(y)).chunk(2, dim=1)
        query = rearrange(query, "b (head c) h w -> b head c (h w)", head=self.num_heads)
        key = rearrange(key, "b (head c) h w -> b head c (h w)", head=self.num_heads)
        value = rearrange(value, "b (head c) h w -> b head c (h w)", head=self.num_heads)
        query = F.normalize(query, dim=-1)
        key = F.normalize(key, dim=-1)
        attention = F.softmax((query @ key.transpose(-2, -1)) * self.temperature, dim=-1)
        output = attention @ value
        output = rearrange(
            output,
            "b head c (h w) -> b (head c) h w",
            head=self.num_heads,
            h=height,
            w=width,
        )
        return self.project_out(output)


class IEL(nn.Module):
    def __init__(self, dim: int, ffn_expansion_factor: float = 2.66, bias: bool = False):
        super().__init__()
        hidden = int(dim * ffn_expansion_factor)
        self.project_in = nn.Conv2d(dim, hidden * 2, 1, bias=bias)
        self.dwconv = nn.Conv2d(hidden * 2, hidden * 2, 3, padding=1, groups=hidden * 2, bias=bias)
        self.dwconv1 = nn.Conv2d(hidden, hidden, 3, padding=1, groups=hidden, bias=bias)
        self.dwconv2 = nn.Conv2d(hidden, hidden, 3, padding=1, groups=hidden, bias=bias)
        self.project_out = nn.Conv2d(hidden, dim, 1, bias=bias)
        self.Tanh = nn.Tanh()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x1, x2 = self.dwconv(self.project_in(x)).chunk(2, dim=1)
        x1 = self.Tanh(self.dwconv1(x1)) + x1
        x2 = self.Tanh(self.dwconv2(x2)) + x2
        return self.project_out(x1 * x2)


class HV_LCA(nn.Module):
    def __init__(self, dim: int, num_heads: int, bias: bool = False):
        super().__init__()
        self.gdfn = IEL(dim)
        self.norm = _LayerNorm(dim)
        self.ffn = CAB(dim, num_heads, bias)

    def forward(self, x: torch.Tensor, y: torch.Tensor) -> torch.Tensor:
        x = x + self.ffn(self.norm(x), self.norm(y))
        return self.gdfn(self.norm(x))


class I_LCA(nn.Module):
    def __init__(self, dim: int, num_heads: int, bias: bool = False):
        super().__init__()
        self.norm = _LayerNorm(dim)
        self.gdfn = IEL(dim)
        self.ffn = CAB(dim, num_heads, bias=bias)

    def forward(self, x: torch.Tensor, y: torch.Tensor) -> torch.Tensor:
        x = x + self.ffn(self.norm(x), self.norm(y))
        return x + self.gdfn(self.norm(x))


class RGB_HVI(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.density_k = nn.Parameter(torch.full([1], 0.2))
        self.gated = False
        self.gated2 = False
        self.alpha = 1.0
        self.alpha_s = 1.3
        self.this_k = 0.0

    def HVIT(self, image: torch.Tensor) -> torch.Tensor:
        eps = 1e-8
        value = image.max(1)[0]
        image_min = image.min(1)[0]
        hue = torch.empty_like(value)
        blue = image[:, 2] == value
        green = image[:, 1] == value
        red = image[:, 0] == value
        hue[blue] = 4.0 + ((image[:, 0] - image[:, 1]) / (value - image_min + eps))[blue]
        hue[green] = 2.0 + ((image[:, 2] - image[:, 0]) / (value - image_min + eps))[green]
        hue[red] = (((image[:, 1] - image[:, 2]) / (value - image_min + eps))[red]) % 6
        hue[image_min == value] = 0.0
        hue = hue.unsqueeze(1) / 6.0
        saturation = (value - image_min) / (value + eps)
        saturation[value == 0] = 0
        saturation = saturation.unsqueeze(1)
        value = value.unsqueeze(1)
        self.this_k = self.density_k.item()
        sensitive = ((value * 0.5 * math.pi).sin() + eps).pow(self.density_k)
        return torch.cat(
            [
                sensitive * saturation * (2.0 * math.pi * hue).cos(),
                sensitive * saturation * (2.0 * math.pi * hue).sin(),
                value,
            ],
            dim=1,
        )

    def PHVIT(self, image: torch.Tensor) -> torch.Tensor:
        eps = 1e-8
        horizontal, vertical, intensity = image[:, 0], image[:, 1], image[:, 2]
        horizontal = horizontal.clamp(-1, 1)
        vertical = vertical.clamp(-1, 1)
        value = intensity.clamp(0, 1)
        sensitive = ((value * 0.5 * math.pi).sin() + eps).pow(self.this_k)
        horizontal = (horizontal / (sensitive + eps)).clamp(-1, 1)
        vertical = (vertical / (sensitive + eps)).clamp(-1, 1)
        hue = (torch.atan2(vertical + eps, horizontal + eps) / (2 * math.pi)) % 1
        saturation = torch.sqrt(horizontal**2 + vertical**2 + eps)
        if self.gated:
            saturation = saturation * self.alpha_s
        saturation = saturation.clamp(0, 1)
        index = torch.floor(hue * 6.0)
        fraction = hue * 6.0 - index
        p = value * (1.0 - saturation)
        q = value * (1.0 - fraction * saturation)
        t = value * (1.0 - (1.0 - fraction) * saturation)
        red = torch.zeros_like(hue)
        green = torch.zeros_like(hue)
        blue = torch.zeros_like(hue)
        choices = (
            (value, t, p),
            (q, value, p),
            (p, value, t),
            (p, q, value),
            (t, p, value),
            (value, p, q),
        )
        for channel_index, (r_value, g_value, b_value) in enumerate(choices):
            mask = index == channel_index
            red[mask], green[mask], blue[mask] = r_value[mask], g_value[mask], b_value[mask]
        rgb = torch.stack([red, green, blue], dim=1)
        return rgb * self.alpha if self.gated2 else rgb


class CIDNet(nn.Module):
    def __init__(
        self,
        channels: tuple[int, ...] = (36, 36, 72, 144),
        heads: tuple[int, ...] = (1, 2, 4, 8),
        norm: bool = False,
    ) -> None:
        super().__init__()
        ch1, ch2, ch3, ch4 = channels
        _, head2, head3, head4 = heads
        self.HVE_block0 = nn.Sequential(
            nn.ReplicationPad2d(1), nn.Conv2d(3, ch1, 3, bias=False)
        )
        self.HVE_block1 = NormDownsample(ch1, ch2, use_norm=norm)
        self.HVE_block2 = NormDownsample(ch2, ch3, use_norm=norm)
        self.HVE_block3 = NormDownsample(ch3, ch4, use_norm=norm)
        self.HVD_block3 = NormUpsample(ch4, ch3, use_norm=norm)
        self.HVD_block2 = NormUpsample(ch3, ch2, use_norm=norm)
        self.HVD_block1 = NormUpsample(ch2, ch1, use_norm=norm)
        self.HVD_block0 = nn.Sequential(
            nn.ReplicationPad2d(1), nn.Conv2d(ch1, 2, 3, bias=False)
        )
        self.IE_block0 = nn.Sequential(
            nn.ReplicationPad2d(1), nn.Conv2d(1, ch1, 3, bias=False)
        )
        self.IE_block1 = NormDownsample(ch1, ch2, use_norm=norm)
        self.IE_block2 = NormDownsample(ch2, ch3, use_norm=norm)
        self.IE_block3 = NormDownsample(ch3, ch4, use_norm=norm)
        self.ID_block3 = NormUpsample(ch4, ch3, use_norm=norm)
        self.ID_block2 = NormUpsample(ch3, ch2, use_norm=norm)
        self.ID_block1 = NormUpsample(ch2, ch1, use_norm=norm)
        self.ID_block0 = nn.Sequential(
            nn.ReplicationPad2d(1), nn.Conv2d(ch1, 1, 3, bias=False)
        )
        self.HV_LCA1 = HV_LCA(ch2, head2)
        self.HV_LCA2 = HV_LCA(ch3, head3)
        self.HV_LCA3 = HV_LCA(ch4, head4)
        self.HV_LCA4 = HV_LCA(ch4, head4)
        self.HV_LCA5 = HV_LCA(ch3, head3)
        self.HV_LCA6 = HV_LCA(ch2, head2)
        self.I_LCA1 = I_LCA(ch2, head2)
        self.I_LCA2 = I_LCA(ch3, head3)
        self.I_LCA3 = I_LCA(ch4, head4)
        self.I_LCA4 = I_LCA(ch4, head4)
        self.I_LCA5 = I_LCA(ch3, head3)
        self.I_LCA6 = I_LCA(ch2, head2)
        self.trans = RGB_HVI()

    def forward(self, image: torch.Tensor) -> torch.Tensor:
        height, width = image.shape[-2:]
        pad_h = (8 - height % 8) % 8
        pad_w = (8 - width % 8) % 8
        mode = "reflect" if height > pad_h and width > pad_w else "replicate"
        padded = F.pad(image, (0, pad_w, 0, pad_h), mode=mode)
        return self._forward_padded(padded)[..., :height, :width]

    def _forward_padded(self, image: torch.Tensor) -> torch.Tensor:
        hvi = self.trans.HVIT(image)
        intensity = hvi[:, 2].unsqueeze(1).to(image.dtype)
        i_enc0 = self.IE_block0(intensity)
        i_enc1 = self.IE_block1(i_enc0)
        hv_0 = self.HVE_block0(hvi)
        hv_1 = self.HVE_block1(hv_0)
        i_enc2 = self.I_LCA1(i_enc1, hv_1)
        hv_2 = self.HV_LCA1(hv_1, i_enc1)
        v_jump1, hv_jump1 = i_enc2, hv_2
        i_enc2, hv_2 = self.IE_block2(i_enc2), self.HVE_block2(hv_2)
        i_enc3 = self.I_LCA2(i_enc2, hv_2)
        hv_3 = self.HV_LCA2(hv_2, i_enc2)
        v_jump2, hv_jump2 = i_enc3, hv_3
        i_enc3, hv_3 = self.IE_block3(i_enc2), self.HVE_block3(hv_2)
        i_enc4 = self.I_LCA3(i_enc3, hv_3)
        hv_4 = self.HV_LCA3(hv_3, i_enc3)
        i_dec4 = self.I_LCA4(i_enc4, hv_4)
        hv_4 = self.HV_LCA4(hv_4, i_enc4)
        hv_3 = self.HVD_block3(hv_4, hv_jump2)
        i_dec3 = self.ID_block3(i_dec4, v_jump2)
        i_dec2 = self.I_LCA5(i_dec3, hv_3)
        hv_2 = self.HV_LCA5(hv_3, i_dec3)
        hv_2 = self.HVD_block2(hv_2, hv_jump1)
        i_dec2 = self.ID_block2(i_dec3, v_jump1)
        i_dec1 = self.I_LCA6(i_dec2, hv_2)
        hv_1 = self.HV_LCA6(hv_2, i_dec2)
        i_dec1 = self.ID_block1(i_dec1, i_enc0)
        i_dec0 = self.ID_block0(i_dec1)
        hv_1 = self.HVD_block1(hv_1, hv_0)
        hv_0 = self.HVD_block0(hv_1)
        return self.trans.PHVIT(torch.cat([hv_0, i_dec0], dim=1) + hvi)

    def HVIT(self, image: torch.Tensor) -> torch.Tensor:
        return self.trans.HVIT(image)
