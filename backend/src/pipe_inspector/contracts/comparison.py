"""Immutable snapshots for comparing two saved inspections."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from .assets import AssetKind, AssetRecord
from .common import ContractModel
from .findings import ReviewStatus
from .intake import TaskRecord
from .tasks import RuntimeStatus

ComparisonLabel = Literal["AJ", "BX", "FS", "PL", "BLOCK", "OTHER", "UNLABELED"]
COMPARISON_LABELS: tuple[ComparisonLabel, ...] = (
    "AJ",
    "BX",
    "FS",
    "PL",
    "BLOCK",
    "OTHER",
    "UNLABELED",
)


class StatusCounts(ContractModel):
    confirmed: int = Field(ge=0)
    tentative: int = Field(ge=0)
    needs_review: int = Field(ge=0)
    rejected: int = Field(ge=0)


class ComparisonRow(ContractModel):
    label: ComparisonLabel
    reference: StatusCounts
    current: StatusCounts
    confirmed_delta: int


def _status_counts(task: TaskRecord, label: ComparisonLabel) -> StatusCounts:
    counts = {status.value: 0 for status in ReviewStatus}
    for finding in task.snapshot.findings:
        finding_label = (
            "UNLABELED"
            if finding.canonical_label is None
            else str(finding.canonical_label)
        )
        if finding_label == label:
            counts[finding.review_status.value] += 1
    return StatusCounts(**counts)


def _comparison_rows(
    reference: TaskRecord, current: TaskRecord
) -> tuple[ComparisonRow, ...]:
    rows = []
    for label in COMPARISON_LABELS:
        reference_counts = _status_counts(reference, label)
        current_counts = _status_counts(current, label)
        rows.append(
            ComparisonRow(
                label=label,
                reference=reference_counts,
                current=current_counts,
                confirmed_delta=(
                    current_counts.confirmed - reference_counts.confirmed
                ),
            )
        )
    return tuple(rows)


class ComparisonResult(ContractModel):
    schema_version: Literal["1"] = "1"
    tool_name: Literal["compare"] = "compare"
    tool_version: Literal["saved-comparison-v1"] = "saved-comparison-v1"
    request_id: UUID
    created_at: AwareDatetime
    attempt: int = Field(ge=1)
    reference: TaskRecord
    current: TaskRecord
    reference_media: AssetRecord
    current_media: AssetRecord
    rows: tuple[ComparisonRow, ...]
    matching_status: Literal["unverified"] = "unverified"
    scope: Literal["saved_findings"] = "saved_findings"

    @model_validator(mode="after")
    def validate_snapshot(self) -> "ComparisonResult":
        if self.reference.snapshot.task_id == self.current.snapshot.task_id:
            raise ValueError("comparison source tasks must be distinct")
        if self.reference.session_id != self.current.session_id:
            raise ValueError("comparison sources must belong to the same session")
        if (
            self.reference.input_asset != self.reference_media.ref
            or self.current.input_asset != self.current_media.ref
        ):
            raise ValueError("comparison media must match each task input asset")
        media = (self.reference_media, self.current_media)
        if any(
            asset.kind not in {AssetKind.IMAGE, AssetKind.VIDEO}
            or asset.parent is not None
            for asset in media
        ):
            raise ValueError("comparison accepts original image or video media only")
        if self.reference_media.kind != self.current_media.kind:
            raise ValueError("comparison source media kinds must match")
        ready = {RuntimeStatus.COMPLETED, RuntimeStatus.PARTIAL}
        if any(task.snapshot.runtime_status not in ready for task in self._tasks):
            raise ValueError("comparison source tasks must be completed or partial")
        if self.rows != _comparison_rows(self.reference, self.current):
            raise ValueError("comparison rows must match the embedded source findings")
        return self

    @property
    def _tasks(self) -> tuple[TaskRecord, TaskRecord]:
        return self.reference, self.current
