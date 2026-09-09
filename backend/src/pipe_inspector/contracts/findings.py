"""Evidence-backed findings, separate from task conclusions and VLM advice."""

from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from .assets import AssetRef
from .common import ContractModel, ImageRegion, NonEmptyStr, Probability
from .detection import DetectionLabel


class ReviewStatus(StrEnum):
    TENTATIVE = "tentative"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    NEEDS_REVIEW = "needs_review"


class FindingSource(StrEnum):
    DETECTOR = "detector"
    OPEN_DETECTOR = "open_detector"
    VLM = "vlm"
    HUMAN = "human"


class EvidenceRef(ContractModel):
    asset: AssetRef
    region: ImageRegion | None = None
    frame_index: int | None = Field(default=None, ge=0)
    timestamp_seconds: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_video_locator(self) -> "EvidenceRef":
        if (self.frame_index is None) != (self.timestamp_seconds is None):
            raise ValueError("video evidence requires both frame index and source timestamp")
        return self


class Finding(ContractModel):
    finding_id: UUID
    task_id: UUID
    canonical_label: DetectionLabel | Literal["OTHER"] | None = None
    open_label: NonEmptyStr | None = None
    review_status: ReviewStatus = ReviewStatus.TENTATIVE
    source: FindingSource
    score: Probability | None = None
    evidence: tuple[EvidenceRef, ...] = Field(min_length=1)
    confirmed_by: Literal["rules", "human"] | None = None

    @model_validator(mode="after")
    def validate_confirmation(self) -> "Finding":
        if self.review_status == ReviewStatus.CONFIRMED:
            if self.canonical_label is None or self.confirmed_by is None:
                raise ValueError(
                    "confirmed findings require a canonical label and confirmation source"
                )
            if self.canonical_label == "OTHER" and self.confirmed_by != "human":
                raise ValueError("OTHER findings require human confirmation")
        elif self.confirmed_by is not None:
            raise ValueError("only confirmed findings may carry confirmation provenance")
        return self
