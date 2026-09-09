from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from .common import ContractModel, ImageRegion


class DetectionLabel(StrEnum):
    AJ = "AJ"
    BX = "BX"
    FS = "FS"
    PL = "PL"
    BLOCK = "BLOCK"


class Detection(ContractModel):
    label: DetectionLabel
    native_label: str = Field(min_length=1)
    score: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    bbox_xyxy: tuple[float, float, float, float]


class DetectionBatch(ContractModel):
    detector_id: str = Field(min_length=1)
    model_version: str = Field(min_length=1)
    label_space_version: str = Field(min_length=1)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    detections: tuple[Detection, ...]

    @model_validator(mode="after")
    def validate_original_image_bounds(self) -> "DetectionBatch":
        for detection in self.detections:
            ImageRegion(width=self.width, height=self.height, bbox_xyxy=detection.bbox_xyxy)
        return self
