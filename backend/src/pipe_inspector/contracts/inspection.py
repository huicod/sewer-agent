"""Structured image-inspection observations, review advice, and decisions."""

from typing import Annotated, Literal

from pydantic import Field, FiniteFloat

from .common import ContractModel, NonEmptyStr
from .detection import DetectionBatch, DetectionLabel
from .findings import Finding
from .tasks import CoverageSummary, InspectionResult

BoundedReason = Annotated[str, Field(min_length=1, max_length=1000)]
ReviewVerdict = Literal["accept", "revise", "reject", "abstain", "need_more_context"]


class QualitySummary(ContractModel):
    mean_luminance: FiniteFloat = Field(ge=0)
    contrast: FiniteFloat = Field(ge=0)
    sharpness: FiniteFloat = Field(ge=0)
    adequate: bool = False
    reasons: tuple[NonEmptyStr, ...] = ()


class ImageObservation(ContractModel):
    detection: DetectionBatch
    quality: QualitySummary


class CandidateReview(ContractModel):
    index: int = Field(ge=0)
    verdict: ReviewVerdict
    proposed_label: DetectionLabel | None = None
    reason: BoundedReason


class AdditionalAnomaly(ContractModel):
    label: DetectionLabel | Literal["OTHER"]
    name: str | None = None
    bbox_xyxy: tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat] | None = None
    reason: BoundedReason


class ImageReview(ContractModel):
    image_assessable: bool
    full_image_audited: bool
    candidates: tuple[CandidateReview, ...]
    additional_anomalies: tuple[AdditionalAnomaly, ...]
    summary: Annotated[str, Field(min_length=1, max_length=2000)]


class InspectionDecision(ContractModel):
    findings: tuple[Finding, ...]
    inspection_result: InspectionResult
    coverage: CoverageSummary
    needs_review: bool
    reasons: tuple[NonEmptyStr, ...]
