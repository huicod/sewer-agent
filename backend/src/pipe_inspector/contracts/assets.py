"""Public media metadata; storage resolves IDs to paths outside these contracts."""

from enum import StrEnum
from uuid import UUID

from pydantic import Field, model_validator

from .common import ContractModel, NonEmptyStr


class AssetRef(ContractModel):
    asset_id: UUID
    version: int = Field(default=1, ge=1)


class AssetKind(StrEnum):
    IMAGE = "image"
    VIDEO = "video"
    FRAME = "frame"
    CROP = "crop"
    ENHANCED_IMAGE = "enhanced_image"
    MASK = "mask"
    DEPTH_MAP = "depth_map"
    REPORT = "report"


class AssetRecord(ContractModel):
    ref: AssetRef
    kind: AssetKind
    media_type: NonEmptyStr
    byte_size: int = Field(gt=0)
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    duration_seconds: float | None = Field(default=None, gt=0)
    parent: AssetRef | None = None

    @model_validator(mode="after")
    def validate_media_metadata(self) -> "AssetRecord":
        if self.kind != AssetKind.REPORT and (self.width is None or self.height is None):
            raise ValueError("visual assets require width and height")
        if self.kind == AssetKind.VIDEO:
            if self.duration_seconds is None:
                raise ValueError("video assets require duration_seconds")
        elif self.duration_seconds is not None:
            raise ValueError("duration_seconds belongs to video assets only")
        if self.kind not in {AssetKind.IMAGE, AssetKind.VIDEO} and self.parent is None:
            raise ValueError("derived assets require their source asset reference")
        if self.parent == self.ref:
            raise ValueError("an asset cannot be its own parent version")
        return self
