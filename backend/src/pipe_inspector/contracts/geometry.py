"""Image-plane geometry evidence, never a calibrated physical measurement."""

from typing import Literal
from uuid import UUID

from pydantic import Field, FiniteFloat, model_validator

from .assets import AssetRecord, AssetRef
from .common import ContractModel, ImageRegion, NonEmptyStr, Probability

DepthDirection = Literal["nearer", "farther"]


class GeometryOptions(ContractModel):
    operation: Literal["segment", "relative_depth", "relative_measure"]
    roi_xyxy: tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat] | None = None
    finding_id: UUID | None = None

    @model_validator(mode="after")
    def validate_selector(self):
        if self.roi_xyxy is not None and self.finding_id is not None:
            raise ValueError("choose either original ROI or finding_id")
        if self.operation == "relative_depth" and (
            self.roi_xyxy is not None or self.finding_id is not None
        ):
            raise ValueError("standalone relative depth uses the whole original image")
        if self.roi_xyxy is not None:
            x1, y1, x2, y2 = self.roi_xyxy
            if not (0 <= x1 < x2 and 0 <= y1 < y2):
                raise ValueError("invalid original pixel ROI")
        return self

    def validate_actions(self, actions, *, enhancement=False):
        requested = [str(action) for action in actions if str(action) in {
            "segment", "relative_depth", "relative_measure"
        }]
        allowed = (
            {("relative_measure",), ("segment", "relative_measure")}
            if self.operation == "relative_measure" else {(self.operation,)}
        )
        if tuple(requested) not in allowed or enhancement:
            raise ValueError("geometry actions/options mismatch or mixed enhancement")


class SegmentationRequest(ContractModel):
    input_asset: AssetRef
    prompt: ImageRegion


class RelativeDepthRequest(ContractModel):
    input_asset: AssetRef


def _derived_pair(source: AssetRef, first: AssetRecord, second: AssetRecord, kind: str):
    if any(a.parent != source or a.kind != kind for a in (first, second)):
        raise ValueError("geometry assets must identify their original parent and kind")
    if len({source, first.ref, second.ref}) != 3:
        raise ValueError("geometry requires distinct derived assets")
    if (first.width, first.height) != (second.width, second.height):
        raise ValueError("geometry asset dimensions differ")


class SegmentationResult(ContractModel):
    schema_version: Literal["1"] = "1"
    input_asset: AssetRef
    prompt: ImageRegion
    mask_asset: AssetRecord
    overlay_asset: AssetRecord
    model_id: NonEmptyStr
    model_version: NonEmptyStr
    model_score: FiniteFloat
    area_pixels: int = Field(gt=0)
    mask_region: ImageRegion
    review_required: Literal[True] = True

    @model_validator(mode="after")
    def validate_mask(self):
        _derived_pair(self.input_asset, self.mask_asset, self.overlay_asset, "mask")
        shape = (self.prompt.width, self.prompt.height)
        if shape != (self.mask_asset.width, self.mask_asset.height) or shape != (
            self.mask_region.width,
            self.mask_region.height,
        ):
            raise ValueError("mask dimensions must match the original prompt")
        if self.area_pixels > self.prompt.width * self.prompt.height:
            raise ValueError("mask area exceeds image area")
        if any(a.media_type != "image/png" for a in (self.mask_asset, self.overlay_asset)):
            raise ValueError("mask evidence must be PNG")
        return self


class RelativeDepthResult(ContractModel):
    schema_version: Literal["1"] = "1"
    input_asset: AssetRef
    raw_asset: AssetRecord
    preview_asset: AssetRecord
    model_id: NonEmptyStr
    model_version: NonEmptyStr
    larger_is: DepthDirection
    raw_min: FiniteFloat
    raw_max: FiniteFloat
    units: Literal["relative_model_units"] = "relative_model_units"
    preview_mapping: Literal["per_image_minmax_white_near"] = "per_image_minmax_white_near"
    review_required: Literal[True] = True

    @model_validator(mode="after")
    def validate_depth(self):
        _derived_pair(self.input_asset, self.raw_asset, self.preview_asset, "depth_map")
        if self.raw_asset.media_type != "application/x-npy" or (
            self.preview_asset.media_type != "image/png"
        ):
            raise ValueError("raw depth must be NPY and preview must be PNG")
        if self.raw_max <= self.raw_min:
            raise ValueError("depth has no relative range")
        return self


class RelativeDepthSummary(ContractModel):
    q10: FiniteFloat
    median: FiniteFloat
    q90: FiniteFloat
    larger_is: DepthDirection
    units: Literal["relative_model_units"] = "relative_model_units"

    @model_validator(mode="after")
    def ordered_quantiles(self):
        if not self.q10 <= self.median <= self.q90:
            raise ValueError("relative depth quantiles must be ordered")
        return self


class GeometryMeasurements(ContractModel):
    input_asset: AssetRef
    area_pixels: int = Field(gt=0)
    image_plane_fraction: Probability
    fraction_denominator: Literal["full_image_pixels"] = "full_image_pixels"
    bbox_width_pixels: FiniteFloat = Field(gt=0)
    bbox_height_pixels: FiniteFloat = Field(gt=0)
    relative_depth: RelativeDepthSummary | None = None
    metric_status: Literal["unavailable_no_calibration"] = "unavailable_no_calibration"
    review_required: Literal[True] = True


class GeometryDetail(ContractModel):
    task_id: UUID
    segmentation: SegmentationResult | None = None
    depth: RelativeDepthResult | None = None
    measurements: GeometryMeasurements | None = None

    @model_validator(mode="after")
    def same_source(self):
        values = [v for v in (self.segmentation, self.depth, self.measurements) if v is not None]
        if len({value.input_asset for value in values}) > 1:
            raise ValueError("geometry evidence must share its original source")
        if self.segmentation and self.depth and (
            self.segmentation.mask_asset.width, self.segmentation.mask_asset.height
        ) != (self.depth.raw_asset.width, self.depth.raw_asset.height):
            raise ValueError("geometry evidence dimensions differ")
        if self.measurements is not None:
            if self.segmentation is None:
                raise ValueError("measurements require a segmentation")
            if self.measurements.area_pixels != self.segmentation.area_pixels:
                raise ValueError("measurement area differs from mask")
            if self.measurements.relative_depth is not None and self.depth is None:
                raise ValueError("relative measurements require depth evidence")
        return self
