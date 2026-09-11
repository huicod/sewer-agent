"""Validated, model-independent output from conditional open detection."""

from typing import Literal

from pydantic import Field, FiniteFloat, model_validator

from .assets import AssetRef
from .common import ContractModel, ImageRegion, NonEmptyStr, Probability
from .detection import DetectionLabel

OPEN_SCAN_VERSION = "open-scan-v1"
OPEN_VOCABULARY_VERSION = "sewer-open-v1"


class OpenCandidate(ContractModel):
    label: DetectionLabel | Literal["OTHER"]
    name: NonEmptyStr
    score: Probability
    bbox_xyxy: tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat]


class OpenDetectionBatch(ContractModel):
    detector_id: NonEmptyStr
    model_version: NonEmptyStr
    vocabulary_version: NonEmptyStr
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    candidates: tuple[OpenCandidate, ...] = Field(max_length=100)

    @model_validator(mode="after")
    def validate_original_image_bounds(self) -> "OpenDetectionBatch":
        for candidate in self.candidates:
            ImageRegion(width=self.width, height=self.height, bbox_xyxy=candidate.bbox_xyxy)
        return self


class OpenScanEvidence(ContractModel):
    batch: OpenDetectionBatch
    asset: AssetRef
    frame_index: int | None = Field(default=None, ge=0)
    timestamp_seconds: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_video_locator(self) -> "OpenScanEvidence":
        if (self.frame_index is None) != (self.timestamp_seconds is None):
            raise ValueError("video evidence requires both frame index and source timestamp")
        return self
