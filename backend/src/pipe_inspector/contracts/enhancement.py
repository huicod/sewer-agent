"""Derived review images; enhancement never constitutes a defect verdict."""

from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import Field, FiniteFloat, model_validator

from .assets import AssetRecord, AssetRef
from .common import ContractModel, ImageRegion, NonEmptyStr
from .inspection import AdditionalAnomaly, QualitySummary


class EnhancementKind(StrEnum):
    LOW_LIGHT = "low_light"
    DEHAZE = "dehaze"
    DEBLUR = "deblur"
    SUPER_RESOLUTION = "super_resolution"


class EnhancementOptions(ContractModel):
    """User-selectable operation; no model, filesystem, device, or budget authority."""

    kind: EnhancementKind
    roi_xyxy: tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat] | None = None

    @model_validator(mode="after")
    def validate_roi(self):
        if self.roi_xyxy is not None:
            x1, y1, x2, y2 = self.roi_xyxy
            if x1 < 0 or y1 < 0 or x2 <= x1 or y2 <= y1:
                raise ValueError("ROI must have positive extent in original pixel coordinates")
        return self


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


class EnhancementProposal(ContractModel):
    """Untrusted VLM suggestion, not execution authority or a defect verdict."""

    options: EnhancementOptions | None = None
    degradation: Literal[
        "low_light", "mixed_dark_noise_blur", "motion_blur", "haze", "small_detail",
        "none", "unsupported",
    ]
    confidence: Literal["clear", "uncertain"]
    evidence: str = Field(min_length=1, max_length=1000)
    inspection_impact: str = Field(min_length=1, max_length=1000)


class EnhancementSelection(ContractModel):
    status: Literal["selected", "skipped"]
    reason: NonEmptyStr
    proposal: EnhancementProposal | None = None
    options: EnhancementOptions | None = None

    @model_validator(mode="after")
    def validate_selection(self):
        if (self.status == "selected") != (self.options is not None):
            raise ValueError("only a selected enhancement may contain executable options")
        if self.options is not None and (
            self.proposal is None or self.options != self.proposal.options
        ):
            raise ValueError("selected options must match the recorded proposal")
        return self


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


class EnhancementReview(ContractModel):
    quality_effect: Literal["improved", "unchanged", "degraded", "uncertain"]
    conflicting_finding_ids: tuple[UUID, ...] = Field(default=(), max_length=128)
    additional_anomalies: tuple[AdditionalAnomaly, ...] = Field(default=(), max_length=12)
    summary: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def validate_references(self):
        if len(set(self.conflicting_finding_ids)) != len(self.conflicting_finding_ids):
            raise ValueError("conflicting finding references must be unique")
        if any(a.name is not None and len(a.name) > 200 for a in self.additional_anomalies):
            raise ValueError("anomaly names must be bounded")
        return self


class EnhancementDetail(ContractModel):
    task_id: UUID
    result: EnhancementResult | None = None
    review: EnhancementReview | None = None
    selection: EnhancementSelection | None = None
