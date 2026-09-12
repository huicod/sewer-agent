"""Derived review images; enhancement never constitutes a defect verdict."""

from enum import StrEnum
from typing import Literal

from pydantic import model_validator

from .assets import AssetRecord, AssetRef
from .common import ContractModel, ImageRegion, NonEmptyStr
from .inspection import QualitySummary


class EnhancementKind(StrEnum):
    DEHAZE = "dehaze"
    DEBLUR = "deblur"
    SUPER_RESOLUTION = "super_resolution"


class EnhancementRequest(ContractModel):
    input_asset: AssetRef
    kind: EnhancementKind
    roi: ImageRegion | None = None

    @model_validator(mode="after")
    def require_super_resolution_roi(self):
        if self.kind == EnhancementKind.SUPER_RESOLUTION and self.roi is None:
            raise ValueError("super_resolution requires an explicit original-image ROI")
        return self


class EnhancementDecision(ContractModel):
    kind: EnhancementKind | None = None
    should_run: bool = False
    reason: NonEmptyStr


class EnhancementResult(ContractModel):
    schema_version: Literal["1"] = "1"
    input_asset: AssetRef
    output_asset: AssetRecord
    kind: EnhancementKind
    model_id: NonEmptyStr
    model_version: NonEmptyStr
    source_region: ImageRegion
    quality_before: QualitySummary
    quality_after: QualitySummary
    review_required: Literal[True] = True

    @model_validator(mode="after")
    def validate_derived_evidence(self):
        if (
            self.output_asset.kind != "enhanced_image"
            or self.output_asset.parent != self.input_asset
            or self.output_asset.ref == self.input_asset
        ):
            raise ValueError("enhancement must create a separate derived asset with its parent")
        return self
