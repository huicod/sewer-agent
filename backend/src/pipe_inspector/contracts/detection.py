from __future__ import annotations

import math
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DetectionLabel(StrEnum):
    AJ = "AJ"
    BX = "BX"
    FS = "FS"
    PL = "PL"
    BLOCK = "BLOCK"


class Detection(BaseModel):
    model_config = ConfigDict(frozen=True)

    label: DetectionLabel
    native_label: str = Field(min_length=1)
    score: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    bbox_xyxy: tuple[float, float, float, float]


class DetectionBatch(BaseModel):
    model_config = ConfigDict(frozen=True)

    detector_id: str = Field(min_length=1)
    model_version: str = Field(min_length=1)
    label_space_version: str = Field(min_length=1)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    detections: tuple[Detection, ...]

    @model_validator(mode="after")
    def validate_original_image_bounds(self) -> "DetectionBatch":
        for detection in self.detections:
            x1, y1, x2, y2 = detection.bbox_xyxy
            if not all(math.isfinite(value) for value in (x1, y1, x2, y2)):
                raise ValueError("bbox coordinates must be finite")
            if not (0.0 <= x1 < x2 <= self.width and 0.0 <= y1 < y2 <= self.height):
                raise ValueError("bbox coordinates must be ordered and within image bounds")
        return self
