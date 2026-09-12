"""DarkIR-m inference architecture.

Source: https://github.com/cidautai/DarkIR/tree/
        0910291157ad04f210481a002cf6bb480f6a7f18
License: MIT, copyright (c) 2025 cidautai.

This is a dependency-free inference copy of ``archs/DarkIR.py``,
``archs/arch_model.py`` and ``archs/arch_util.py``. Training-only code and
unused adapters are omitted; parameter names and inference math are unchanged.
"""

from __future__ import annotations

import torch
import torch.nn.functional as F
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
        self.register_parameter("weight", nn.Parameter(torch.ones(channels)))
        self.register_parameter("bias", nn.Parameter(torch.zeros(channels)))
        self.eps = eps

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return _LayerNormFunction.apply(x, self.weight, self.bias, self.eps)


class CustomSequential(nn.Module):
    def __init__(self, *modules: nn.Module) -> None:
        super().__init__()
        self.modules_list = nn.ModuleList(modules)

    def forward(self, x: torch.Tensor, use_adapter: bool = False) -> torch.Tensor:
        del use_adapter
        for module in self.modules_list:
            x = module(x)
        return x


class SimpleGate(nn.Module):
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x1, x2 = x.chunk(2, dim=1)
        return x1 * x2


class FreMLP(nn.Module):
    def __init__(self, nc: int, expand: int = 2) -> None:
        super().__init__()
        self.process1 = nn.Sequential(
            nn.Conv2d(nc, expand * nc, 1, 1, 0),
            nn.LeakyReLU(0.1, inplace=True),
            nn.Conv2d(expand * nc, nc, 1, 1, 0),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        height, width = x.shape[-2:]
        frequency = torch.fft.rfft2(x, norm="backward")
        magnitude = self.process1(torch.abs(frequency))
        phase = torch.angle(frequency)
        complex_output = torch.complex(magnitude * torch.cos(phase), magnitude * torch.sin(phase))
        return torch.fft.irfft2(complex_output, s=(height, width), norm="backward")


class Branch(nn.Module):
    def __init__(self, c: int, DW_Expand: int, dilation: int = 1) -> None:
        super().__init__()
        channels = DW_Expand * c
        self.branch = nn.Sequential(
            nn.Conv2d(
                channels,
                channels,
                kernel_size=3,
                padding=dilation,
                groups=channels,
                bias=True,
                dilation=dilation,
            )
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.branch(x)


class DBlock(nn.Module):
    def __init__(
        self,
        c: int,
        DW_Expand: int = 2,
        FFN_Expand: int = 2,
        dilations: tuple[int, ...] = (1,),
        extra_depth_wise: bool = False,
    ) -> None:
        super().__init__()
        self.dw_channel = DW_Expand * c
        self.conv1 = nn.Conv2d(c, self.dw_channel, 1, bias=True)
        self.extra_conv = (
            nn.Conv2d(self.dw_channel, self.dw_channel, 3, padding=1, groups=c, bias=True)
            if extra_depth_wise
            else nn.Identity()
        )
        self.branches = nn.ModuleList(
            Branch(self.dw_channel, DW_Expand=1, dilation=dilation)
            for dilation in dilations
        )
        self.sca = nn.Sequential(
            nn.AdaptiveAvgPool2d(1),
            nn.Conv2d(self.dw_channel // 2, self.dw_channel // 2, 1, bias=True),
        )
        self.sg1 = SimpleGate()
        self.sg2 = SimpleGate()
        self.conv3 = nn.Conv2d(self.dw_channel // 2, c, 1, bias=True)
        ffn_channel = FFN_Expand * c
        self.conv4 = nn.Conv2d(c, ffn_channel, 1, bias=True)
        self.conv5 = nn.Conv2d(ffn_channel // 2, c, 1, bias=True)
        self.norm1 = LayerNorm2d(c)
        self.norm2 = LayerNorm2d(c)
        self.gamma = nn.Parameter(torch.zeros((1, c, 1, 1)))
        self.beta = nn.Parameter(torch.zeros((1, c, 1, 1)))

    def forward(self, inp: torch.Tensor) -> torch.Tensor:
        x = self.extra_conv(self.conv1(self.norm1(inp)))
        branches = sum((branch(x) for branch in self.branches), start=torch.zeros_like(x))
        gated = self.sg1(branches)
        y = inp + self.beta * self.conv3(self.sca(gated) * gated)
        x = self.sg2(self.conv4(self.norm2(y)))
        return y + self.conv5(x) * self.gamma


class EBlock(nn.Module):
    def __init__(
        self,
        c: int,
        DW_Expand: int = 2,
        dilations: tuple[int, ...] = (1,),
        extra_depth_wise: bool = False,
    ) -> None:
        super().__init__()
        self.dw_channel = DW_Expand * c
        self.extra_conv = (
            nn.Conv2d(c, c, 3, padding=1, groups=c, bias=True)
            if extra_depth_wise
            else nn.Identity()
        )
        self.conv1 = nn.Conv2d(c, self.dw_channel, 1, bias=True)
        self.branches = nn.ModuleList(
            Branch(c, DW_Expand=DW_Expand, dilation=dilation) for dilation in dilations
        )
        self.sca = nn.Sequential(
            nn.AdaptiveAvgPool2d(1),
            nn.Conv2d(self.dw_channel // 2, self.dw_channel // 2, 1, bias=True),
        )
        self.sg1 = SimpleGate()
        self.conv3 = nn.Conv2d(self.dw_channel // 2, c, 1, bias=True)
        self.norm1 = LayerNorm2d(c)
        self.norm2 = LayerNorm2d(c)
        self.freq = FreMLP(nc=c, expand=2)
        self.gamma = nn.Parameter(torch.zeros((1, c, 1, 1)))
        self.beta = nn.Parameter(torch.zeros((1, c, 1, 1)))

    def forward(self, inp: torch.Tensor) -> torch.Tensor:
        x = self.conv1(self.extra_conv(self.norm1(inp)))
        branches = sum((branch(x) for branch in self.branches), start=torch.zeros_like(x))
        gated = self.sg1(branches)
        y = inp + self.beta * self.conv3(self.sca(gated) * gated)
        x = y * self.freq(self.norm2(y))
        return y + x * self.gamma


class DarkIR(nn.Module):
    """Official medium (width 32) DarkIR network used by the HF checkpoint."""

    def __init__(
        self,
        img_channel: int = 3,
        width: int = 32,
        middle_blk_num_enc: int = 2,
        middle_blk_num_dec: int = 2,
        enc_blk_nums: tuple[int, ...] = (1, 2, 3),
        dec_blk_nums: tuple[int, ...] = (3, 1, 1),
        dilations: tuple[int, ...] = (1, 4, 9),
        extra_depth_wise: bool = True,
    ) -> None:
        super().__init__()
        self.intro = nn.Conv2d(img_channel, width, 3, padding=1, bias=True)
        self.ending = nn.Conv2d(width, img_channel, 3, padding=1, bias=True)
        self.encoders = nn.ModuleList()
        self.decoders = nn.ModuleList()
        self.middle_blks = nn.ModuleList()
        self.ups = nn.ModuleList()
        self.downs = nn.ModuleList()
        channels = width
        for blocks in enc_blk_nums:
            self.encoders.append(
                CustomSequential(
                    *(EBlock(channels, extra_depth_wise=extra_depth_wise) for _ in range(blocks))
                )
            )
            self.downs.append(nn.Conv2d(channels, 2 * channels, 2, 2))
            channels *= 2
        self.middle_blks_enc = CustomSequential(
            *(
                EBlock(channels, extra_depth_wise=extra_depth_wise)
                for _ in range(middle_blk_num_enc)
            )
        )
        self.middle_blks_dec = CustomSequential(
            *(
                DBlock(channels, dilations=dilations, extra_depth_wise=extra_depth_wise)
                for _ in range(middle_blk_num_dec)
            )
        )
        for blocks in dec_blk_nums:
            self.ups.append(
                nn.Sequential(nn.Conv2d(channels, channels * 2, 1, bias=False), nn.PixelShuffle(2))
            )
            channels //= 2
            self.decoders.append(
                CustomSequential(
                    *(
                        DBlock(channels, dilations=dilations, extra_depth_wise=extra_depth_wise)
                        for _ in range(blocks)
                    )
                )
            )
        self.padder_size = 2 ** len(self.encoders)
        self.side_out = nn.Conv2d(width * 2 ** len(self.encoders), img_channel, 3, padding=1)

    def forward(self, input_tensor: torch.Tensor) -> torch.Tensor:
        height, width = input_tensor.shape[-2:]
        padded = self.check_image_size(input_tensor)
        x = self.intro(padded)
        skips = []
        for encoder, down in zip(self.encoders, self.downs, strict=True):
            x = encoder(x)
            skips.append(x)
            x = down(x)
        light = self.middle_blks_enc(x)
        x = self.middle_blks_dec(light) + light
        for decoder, up, skip in zip(self.decoders, self.ups, reversed(skips), strict=True):
            x = decoder(up(x) + skip)
        return (self.ending(x) + padded)[..., :height, :width]

    def check_image_size(self, x: torch.Tensor) -> torch.Tensor:
        height, width = x.shape[-2:]
        pad_h = (self.padder_size - height % self.padder_size) % self.padder_size
        pad_w = (self.padder_size - width % self.padder_size) % self.padder_size
        return F.pad(x, (0, pad_w, 0, pad_h), value=0)
