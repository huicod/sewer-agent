"""DehazeFormer MCT architecture.

Source: https://huggingface.co/spaces/IDKiro/DehazeFormer_Demo/blob/
        8e0b26c7732e2b0dc104f69c5ea30f913c878a89/models/dehazeformer.py
License: MIT, copyright (c) 2023 IDKiro.

Formatting and annotations differ from upstream. ``MCT.get_coord`` additionally creates
its coordinate tensors on the input device and with the input dtype; the upstream CPU
linspace tensors otherwise fail when the pinned model runs on CUDA.
"""

from __future__ import annotations

from collections.abc import Sequence

import torch
import torch.nn.functional as F
from torch import nn


class RLN(nn.Module):
    """Revised LayerNorm."""

    def __init__(self, dim: int, eps: float = 1e-5, detach_grad: bool = False) -> None:
        super().__init__()
        self.eps = eps
        self.detach_grad = detach_grad
        self.weight = nn.Parameter(torch.ones((1, dim, 1, 1)))
        self.bias = nn.Parameter(torch.zeros((1, dim, 1, 1)))
        self.meta1 = nn.Conv2d(1, dim, 1)
        self.meta2 = nn.Conv2d(1, dim, 1)

    def forward(
        self, input_tensor: torch.Tensor
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        mean = torch.mean(input_tensor, dim=(1, 2, 3), keepdim=True)
        std = torch.sqrt(
            (input_tensor - mean).pow(2).mean(dim=(1, 2, 3), keepdim=True) + self.eps
        )
        normalized_input = (input_tensor - mean) / std
        if self.detach_grad:
            rescale, rebias = self.meta1(std.detach()), self.meta2(mean.detach())
        else:
            rescale, rebias = self.meta1(std), self.meta2(mean)
        output = normalized_input * self.weight + self.bias
        return output, rescale, rebias


class Mlp(nn.Module):
    def __init__(
        self,
        network_depth: int,
        in_features: int,
        hidden_features: int | None = None,
        out_features: int | None = None,
    ) -> None:
        super().__init__()
        del network_depth
        out_features = out_features or in_features
        hidden_features = hidden_features or in_features
        self.mlp = nn.Sequential(
            nn.Conv2d(in_features, hidden_features, 1),
            nn.ReLU(True),
            nn.Conv2d(hidden_features, out_features, 1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.mlp(x)


def window_partition(x: torch.Tensor, window_size: int) -> torch.Tensor:
    batch, height, width, channels = x.shape
    x = x.view(
        batch,
        height // window_size,
        window_size,
        width // window_size,
        window_size,
        channels,
    )
    return (
        x.permute(0, 1, 3, 2, 4, 5)
        .contiguous()
        .view(-1, window_size**2, channels)
    )


def window_reverse(
    windows: torch.Tensor, window_size: int, height: int, width: int
) -> torch.Tensor:
    batch = int(windows.shape[0] / (height * width / window_size / window_size))
    x = windows.view(
        batch,
        height // window_size,
        width // window_size,
        window_size,
        window_size,
        -1,
    )
    return x.permute(0, 1, 3, 2, 4, 5).contiguous().view(batch, height, width, -1)


def get_relative_positions(window_size: int) -> torch.Tensor:
    coords_h = torch.arange(window_size)
    coords_w = torch.arange(window_size)
    coords = torch.stack(torch.meshgrid([coords_h, coords_w], indexing="ij"))
    coords_flatten = torch.flatten(coords, 1)
    relative_positions = coords_flatten[:, :, None] - coords_flatten[:, None, :]
    relative_positions = relative_positions.permute(1, 2, 0).contiguous()
    return torch.sign(relative_positions) * torch.log(1.0 + relative_positions.abs())


class WindowAttention(nn.Module):
    def __init__(self, dim: int, window_size: int, num_heads: int) -> None:
        super().__init__()
        self.dim = dim
        self.window_size = window_size
        self.num_heads = num_heads
        self.scale = (dim // num_heads) ** -0.5
        self.register_buffer("relative_positions", get_relative_positions(window_size))
        self.meta = nn.Sequential(
            nn.Linear(2, 256, bias=True),
            nn.ReLU(True),
            nn.Linear(256, num_heads, bias=True),
        )
        self.softmax = nn.Softmax(dim=-1)

    def forward(self, qkv: torch.Tensor) -> torch.Tensor:
        batch, count, _ = qkv.shape
        qkv = qkv.reshape(
            batch, count, 3, self.num_heads, self.dim // self.num_heads
        ).permute(2, 0, 3, 1, 4)
        q, k, v = qkv[0], qkv[1], qkv[2]
        attention = (q * self.scale) @ k.transpose(-2, -1)
        position_bias = self.meta(self.relative_positions)
        position_bias = position_bias.permute(2, 0, 1).contiguous()
        attention = self.softmax(attention + position_bias.unsqueeze(0))
        return (attention @ v).transpose(1, 2).reshape(batch, count, self.dim)


class Attention(nn.Module):
    def __init__(
        self,
        network_depth: int,
        dim: int,
        num_heads: int,
        window_size: int,
        shift_size: int,
        use_attn: bool = False,
        conv_type: str | None = None,
    ) -> None:
        super().__init__()
        del network_depth
        self.dim = dim
        self.head_dim = dim // num_heads
        self.num_heads = num_heads
        self.window_size = window_size
        self.shift_size = shift_size
        self.use_attn = use_attn
        self.conv_type = conv_type

        if conv_type == "Conv":
            self.conv = nn.Sequential(
                nn.Conv2d(dim, dim, kernel_size=3, padding=1, padding_mode="reflect"),
                nn.ReLU(True),
                nn.Conv2d(dim, dim, kernel_size=3, padding=1, padding_mode="reflect"),
            )
        if conv_type == "DWConv":
            self.conv = nn.Conv2d(
                dim,
                dim,
                kernel_size=5,
                padding=2,
                groups=dim,
                padding_mode="reflect",
            )
        if conv_type == "DWConv" or use_attn:
            self.V = nn.Conv2d(dim, dim, 1)
            self.proj = nn.Conv2d(dim, dim, 1)
        if use_attn:
            self.QK = nn.Conv2d(dim, dim * 2, 1)
            self.attn = WindowAttention(dim, window_size, num_heads)

    def check_size(self, x: torch.Tensor, shift: bool = False) -> torch.Tensor:
        _, _, height, width = x.size()
        pad_height = (self.window_size - height % self.window_size) % self.window_size
        pad_width = (self.window_size - width % self.window_size) % self.window_size
        if shift:
            right = (self.window_size - self.shift_size + pad_width) % self.window_size
            bottom = (self.window_size - self.shift_size + pad_height) % self.window_size
            return F.pad(
                x,
                (self.shift_size, right, self.shift_size, bottom),
                mode="reflect",
            )
        return F.pad(x, (0, pad_width, 0, pad_height), mode="reflect")

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        _, _, height, width = x.shape
        if self.conv_type == "DWConv" or self.use_attn:
            value = self.V(x)
        if self.use_attn:
            qkv = torch.cat([self.QK(x), value], dim=1)
            shifted_qkv = self.check_size(qkv, self.shift_size > 0)
            padded_height, padded_width = shifted_qkv.shape[2:]
            windows = window_partition(shifted_qkv.permute(0, 2, 3, 1), self.window_size)
            attention_windows = self.attn(windows)
            shifted_output = window_reverse(
                attention_windows, self.window_size, padded_height, padded_width
            )
            output = shifted_output[
                :,
                self.shift_size : self.shift_size + height,
                self.shift_size : self.shift_size + width,
                :,
            ]
            attention_output = output.permute(0, 3, 1, 2)
            if self.conv_type in {"Conv", "DWConv"}:
                return self.proj(self.conv(value) + attention_output)
            return self.proj(attention_output)
        if self.conv_type == "Conv":
            return self.conv(x)
        if self.conv_type == "DWConv":
            return self.proj(self.conv(value))
        raise RuntimeError("DehazeFormer attention block has no configured operation")


class TransformerBlock(nn.Module):
    def __init__(
        self,
        network_depth: int,
        dim: int,
        num_heads: int,
        mlp_ratio: float = 4.0,
        norm_layer: type[nn.Module] = nn.LayerNorm,
        mlp_norm: bool = False,
        window_size: int = 8,
        shift_size: int = 0,
        use_attn: bool = True,
        conv_type: str | None = None,
    ) -> None:
        super().__init__()
        self.use_attn = use_attn
        self.mlp_norm = mlp_norm
        self.norm1 = norm_layer(dim) if use_attn else nn.Identity()
        self.attn = Attention(
            network_depth,
            dim,
            num_heads=num_heads,
            window_size=window_size,
            shift_size=shift_size,
            use_attn=use_attn,
            conv_type=conv_type,
        )
        self.norm2 = norm_layer(dim) if use_attn and mlp_norm else nn.Identity()
        self.mlp = Mlp(network_depth, dim, hidden_features=int(dim * mlp_ratio))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        identity = x
        if self.use_attn:
            x, rescale, rebias = self.norm1(x)
        x = self.attn(x)
        if self.use_attn:
            x = x * rescale + rebias
        x = identity + x

        identity = x
        if self.use_attn and self.mlp_norm:
            x, rescale, rebias = self.norm2(x)
        x = self.mlp(x)
        if self.use_attn and self.mlp_norm:
            x = x * rescale + rebias
        return identity + x


class BasicLayer(nn.Module):
    def __init__(
        self,
        network_depth: int,
        dim: int,
        depth: int,
        num_heads: int,
        mlp_ratio: float = 4.0,
        norm_layer: type[nn.Module] = nn.LayerNorm,
        window_size: int = 8,
        attn_ratio: float = 0.0,
        attn_loc: str = "last",
        conv_type: str | None = None,
    ) -> None:
        super().__init__()
        self.dim = dim
        self.depth = depth
        attn_depth = attn_ratio * depth
        if attn_loc == "last":
            use_attns = [index >= depth - attn_depth for index in range(depth)]
        elif attn_loc == "first":
            use_attns = [index < attn_depth for index in range(depth)]
        elif attn_loc == "middle":
            use_attns = [
                (depth - attn_depth) // 2 <= index < (depth + attn_depth) // 2
                for index in range(depth)
            ]
        else:
            raise ValueError(f"unsupported attention location: {attn_loc!r}")
        self.blocks = nn.ModuleList(
            [
                TransformerBlock(
                    network_depth=network_depth,
                    dim=dim,
                    num_heads=num_heads,
                    mlp_ratio=mlp_ratio,
                    norm_layer=norm_layer,
                    window_size=window_size,
                    shift_size=0 if index % 2 == 0 else window_size // 2,
                    use_attn=use_attns[index],
                    conv_type=conv_type,
                )
                for index in range(depth)
            ]
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        for block in self.blocks:
            x = block(x)
        return x


class PatchEmbed(nn.Module):
    def __init__(
        self,
        patch_size: int = 4,
        in_chans: int = 3,
        embed_dim: int = 96,
        kernel_size: int | None = None,
    ) -> None:
        super().__init__()
        self.in_chans = in_chans
        self.embed_dim = embed_dim
        kernel_size = patch_size if kernel_size is None else kernel_size
        self.proj = nn.Conv2d(
            in_chans,
            embed_dim,
            kernel_size=kernel_size,
            stride=patch_size,
            padding=(kernel_size - patch_size + 1) // 2,
            padding_mode="reflect",
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.proj(x)


class PatchUnEmbed(nn.Module):
    def __init__(
        self,
        patch_size: int = 4,
        out_chans: int = 3,
        embed_dim: int = 96,
        kernel_size: int | None = None,
    ) -> None:
        super().__init__()
        self.out_chans = out_chans
        self.embed_dim = embed_dim
        kernel_size = 1 if kernel_size is None else kernel_size
        self.proj = nn.Sequential(
            nn.Conv2d(
                embed_dim,
                out_chans * patch_size**2,
                kernel_size=kernel_size,
                padding=kernel_size // 2,
                padding_mode="reflect",
            ),
            nn.PixelShuffle(patch_size),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.proj(x)


class SKFusion(nn.Module):
    def __init__(self, dim: int, height: int = 2, reduction: int = 8) -> None:
        super().__init__()
        self.height = height
        reduced_dim = max(dim // reduction, 4)
        self.avg_pool = nn.AdaptiveAvgPool2d(1)
        self.mlp = nn.Sequential(
            nn.Conv2d(dim, reduced_dim, 1, bias=False),
            nn.ReLU(),
            nn.Conv2d(reduced_dim, dim * height, 1, bias=False),
        )
        self.softmax = nn.Softmax(dim=1)

    def forward(self, input_features: Sequence[torch.Tensor]) -> torch.Tensor:
        batch, channels, height, width = input_features[0].shape
        features = torch.cat(list(input_features), dim=1)
        features = features.view(batch, self.height, channels, height, width)
        feature_sum = torch.sum(features, dim=1)
        attention = self.mlp(self.avg_pool(feature_sum))
        attention = self.softmax(attention.view(batch, self.height, channels, 1, 1))
        return torch.sum(features * attention, dim=1)


class DehazeFormer(nn.Module):
    def __init__(
        self,
        in_chans: int = 3,
        out_chans: int = 3,
        window_size: int = 8,
        embed_dims: Sequence[int] = (24, 48, 96, 48, 24),
        mlp_ratios: Sequence[float] = (2.0, 2.0, 4.0, 2.0, 2.0),
        depths: Sequence[int] = (4, 4, 8, 4, 4),
        num_heads: Sequence[int] = (2, 4, 6, 4, 2),
        attn_ratio: Sequence[float] = (1.0, 1.0, 1.0, 1.0, 1.0),
        conv_type: Sequence[str] = ("DWConv", "DWConv", "DWConv", "DWConv", "DWConv"),
        norm_layer: Sequence[type[nn.Module]] = (RLN, RLN, RLN, RLN, RLN),
    ) -> None:
        super().__init__()
        self.patch_size = 4
        self.window_size = window_size
        self.mlp_ratios = mlp_ratios
        network_depth = sum(depths)

        self.patch_embed = PatchEmbed(
            patch_size=1, in_chans=in_chans, embed_dim=embed_dims[0], kernel_size=3
        )
        self.layer1 = BasicLayer(
            network_depth,
            embed_dims[0],
            depths[0],
            num_heads[0],
            mlp_ratio=mlp_ratios[0],
            norm_layer=norm_layer[0],
            window_size=window_size,
            attn_ratio=attn_ratio[0],
            attn_loc="last",
            conv_type=conv_type[0],
        )
        self.patch_merge1 = PatchEmbed(
            patch_size=2, in_chans=embed_dims[0], embed_dim=embed_dims[1]
        )
        self.skip1 = nn.Conv2d(embed_dims[0], embed_dims[0], 1)
        self.layer2 = BasicLayer(
            network_depth,
            embed_dims[1],
            depths[1],
            num_heads[1],
            mlp_ratio=mlp_ratios[1],
            norm_layer=norm_layer[1],
            window_size=window_size,
            attn_ratio=attn_ratio[1],
            attn_loc="last",
            conv_type=conv_type[1],
        )
        self.patch_merge2 = PatchEmbed(
            patch_size=2, in_chans=embed_dims[1], embed_dim=embed_dims[2]
        )
        self.skip2 = nn.Conv2d(embed_dims[1], embed_dims[1], 1)
        self.layer3 = BasicLayer(
            network_depth,
            embed_dims[2],
            depths[2],
            num_heads[2],
            mlp_ratio=mlp_ratios[2],
            norm_layer=norm_layer[2],
            window_size=window_size,
            attn_ratio=attn_ratio[2],
            attn_loc="last",
            conv_type=conv_type[2],
        )
        self.patch_split1 = PatchUnEmbed(
            patch_size=2, out_chans=embed_dims[3], embed_dim=embed_dims[2]
        )
        if embed_dims[1] != embed_dims[3]:
            raise ValueError("DehazeFormer fusion dimensions must match")
        self.fusion1 = SKFusion(embed_dims[3])
        self.layer4 = BasicLayer(
            network_depth,
            embed_dims[3],
            depths[3],
            num_heads[3],
            mlp_ratio=mlp_ratios[3],
            norm_layer=norm_layer[3],
            window_size=window_size,
            attn_ratio=attn_ratio[3],
            attn_loc="last",
            conv_type=conv_type[3],
        )
        self.patch_split2 = PatchUnEmbed(
            patch_size=2, out_chans=embed_dims[4], embed_dim=embed_dims[3]
        )
        if embed_dims[0] != embed_dims[4]:
            raise ValueError("DehazeFormer fusion dimensions must match")
        self.fusion2 = SKFusion(embed_dims[4])
        self.layer5 = BasicLayer(
            network_depth,
            embed_dims[4],
            depths[4],
            num_heads[4],
            mlp_ratio=mlp_ratios[4],
            norm_layer=norm_layer[4],
            window_size=window_size,
            attn_ratio=attn_ratio[4],
            attn_loc="last",
            conv_type=conv_type[4],
        )
        self.patch_unembed = PatchUnEmbed(
            patch_size=1, out_chans=out_chans, embed_dim=embed_dims[4], kernel_size=3
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.layer1(self.patch_embed(x))
        skip1 = x
        x = self.layer2(self.patch_merge1(x))
        skip2 = x
        x = self.layer3(self.patch_merge2(x))
        x = self.patch_split1(x)
        x = self.fusion1([x, self.skip2(skip2)]) + x
        x = self.layer4(x)
        x = self.patch_split2(x)
        x = self.fusion2([x, self.skip1(skip1)]) + x
        x = self.layer5(x)
        return self.patch_unembed(x)


class MCT(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.ts = 256
        self.l = 8
        self.dims = 3 * 3 * self.l
        self.basenet = DehazeFormer(3, self.dims)

    @staticmethod
    def get_coord(x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        batch, _, height, width = x.size()
        coord_h, coord_w = torch.meshgrid(
            torch.linspace(-1, 1, height, device=x.device, dtype=x.dtype),
            torch.linspace(-1, 1, width, device=x.device, dtype=x.dtype),
            indexing="ij",
        )
        coord_h = coord_h.unsqueeze(0).unsqueeze(1).repeat(batch, 1, 1, 1)
        coord_w = coord_w.unsqueeze(0).unsqueeze(1).repeat(batch, 1, 1, 1)
        return coord_w.detach(), coord_h.detach()

    def mapping(self, x: torch.Tensor, param: torch.Tensor) -> torch.Tensor:
        curve = torch.stack(torch.chunk(param, 3, dim=1), dim=1)
        curve_list = list(torch.chunk(curve, 3, dim=2))
        input_list = list(torch.chunk(x.detach(), 3, dim=1))
        coord_w, coord_h = self.get_coord(x)
        grid_list = [
            torch.stack([coord_w, coord_h, input_channel], dim=4)
            for input_channel in input_list
        ]
        outputs = [
            F.grid_sample(curve_item, grid, "bilinear", "border", True)
            for curve_item, grid in zip(curve_list, grid_list, strict=True)
        ]
        return torch.stack(outputs).sum(dim=0).squeeze(2)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        downsampled = F.interpolate(x, (self.ts, self.ts), mode="area")
        return self.mapping(x, self.basenet(downsampled))
