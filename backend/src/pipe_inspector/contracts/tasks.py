"""Persistable task projections; Temporal, not this model, owns transitions."""

from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from .common import ContractModel, NonEmptyStr, Probability
from .findings import Finding, ReviewStatus


class RuntimeStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_REVIEW = "waiting_review"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class InspectionResult(StrEnum):
    PENDING = "pending"
    NORMAL = "normal"
    DEFECTS_FOUND = "defects_found"
    INCONCLUSIVE = "inconclusive"


class TaskStage(StrEnum):
    QUEUED = "queued"
    VALIDATING = "validating"
    PLANNING = "planning"
    DECODING = "decoding"
    DETECTING = "detecting"
    AGGREGATING = "aggregating"
    VERIFYING = "verifying"
    TOOLS = "tools"
    HUMAN_REVIEW = "human_review"
    REPORTING = "reporting"
    FINISHED = "finished"


class CoverageSummary(ContractModel):
    sufficient_coverage: bool = False
    sufficient_quality: bool = False
    negative_audit_complete: bool = False


class TaskSnapshot(ContractModel):
    schema_version: Literal["1"] = "1"
    task_id: UUID
    runtime_status: RuntimeStatus = RuntimeStatus.QUEUED
    inspection_result: InspectionResult = InspectionResult.PENDING
    stage: TaskStage = TaskStage.QUEUED
    stage_progress: Probability = 0
    overall_progress: Probability = 0
    processed_frames: int = Field(default=0, ge=0)
    processed_chunks: int = Field(default=0, ge=0)
    active_tool: NonEmptyStr | None = None
    retry_count: int = Field(default=0, ge=0)
    waiting_reason: NonEmptyStr | None = None
    updated_at: AwareDatetime
    findings: tuple[Finding, ...] = ()
    coverage: CoverageSummary = Field(default_factory=CoverageSummary)

    @property
    def is_terminal(self) -> bool:
        return self.runtime_status in {
            RuntimeStatus.COMPLETED,
            RuntimeStatus.PARTIAL,
            RuntimeStatus.FAILED,
            RuntimeStatus.CANCELLED,
        }

    @model_validator(mode="after")
    def validate_projection(self) -> "TaskSnapshot":
        if any(finding.task_id != self.task_id for finding in self.findings):
            raise ValueError("findings must belong to this task")
        if len({finding.finding_id for finding in self.findings}) != len(self.findings):
            raise ValueError("duplicate finding IDs in task snapshot")
        confirmed = any(f.review_status == ReviewStatus.CONFIRMED for f in self.findings)
        unresolved = any(
            f.review_status in {ReviewStatus.TENTATIVE, ReviewStatus.NEEDS_REVIEW}
            for f in self.findings
        )
        if self.inspection_result == InspectionResult.NORMAL:
            if (
                not (
                    self.coverage.sufficient_coverage
                    and self.coverage.sufficient_quality
                    and self.coverage.negative_audit_complete
                )
                or confirmed
                or unresolved
            ):
                raise ValueError(
                    "normal requires sufficient audited coverage and resolved findings"
                )
        if self.inspection_result == InspectionResult.DEFECTS_FOUND and not confirmed:
            raise ValueError("defects_found requires at least one confirmed finding")
        if self.runtime_status == RuntimeStatus.WAITING_REVIEW:
            if not self.waiting_reason or self.stage != TaskStage.HUMAN_REVIEW:
                raise ValueError("waiting_review requires human_review stage and a reason")
        if self.runtime_status == RuntimeStatus.COMPLETED:
            if self.inspection_result == InspectionResult.PENDING or unresolved:
                raise ValueError("completed tasks require a conclusion and resolved findings")
        return self
