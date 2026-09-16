"""Read-only inspection lineage, separate from final business findings."""

from typing import Literal
from uuid import UUID

from pydantic import Field, FiniteFloat

from .assets import AssetRef
from .common import ContractModel
from .detection import DetectionLabel
from .findings import Finding
from .inspection import AdditionalAnomaly, CandidateReview, ImageObservation, ImageReview


class EvidenceCandidate(CandidateReview):
    evidence_strength: Literal["clear", "uncertain"] | None = None


class EvidenceAnomaly(AdditionalAnomaly):
    evidence_strength: Literal["clear", "uncertain"] | None = None


class EvidenceReview(ImageReview):
    candidates: tuple[EvidenceCandidate, ...]
    additional_anomalies: tuple[EvidenceAnomaly, ...]


class FusionChange(ContractModel):
    index: int | None = Field(default=None, ge=0)
    additional_index: int | None = Field(default=None, ge=0)
    action: str
    pending: bool
    label: DetectionLabel | Literal["OTHER"]
    bbox_xyxy: tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat] | None = None
    reason: str
    vlm_verdict: str | None = None
    proposed_label: DetectionLabel | None = None
    gate: str | None = None


class ImageEvidence(ContractModel):
    task_id: UUID
    input_asset: AssetRef
    parent_task_id: UUID | None = None
    observation: ImageObservation | None = None
    review: EvidenceReview | None = None
    fusion_changes: tuple[FusionChange, ...] = ()
    findings: tuple[Finding, ...] = ()
