"""Shared value constraints; importing contracts never performs I/O."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

NonEmptyStr = Annotated[str, Field(min_length=1)]
Probability = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


class ContractModel(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", allow_inf_nan=False, str_strip_whitespace=True
    )


class ImageRegion(ContractModel):
    """A box measured in the original image's pixels, never normalized coordinates."""

    width: int = Field(gt=0)
    height: int = Field(gt=0)
    bbox_xyxy: tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat]

    @model_validator(mode="after")
    def validate_bounds(self) -> "ImageRegion":
        x1, y1, x2, y2 = self.bbox_xyxy
        if not (0 <= x1 < x2 <= self.width and 0 <= y1 < y2 <= self.height):
            raise ValueError("bbox coordinates must be ordered and within image bounds")
        return self
