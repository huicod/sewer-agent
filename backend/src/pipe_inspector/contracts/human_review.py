"""Validated complete-batch human review contracts."""

import json
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from .common import ContractModel, NonEmptyStr
from .detection import DetectionLabel
from .tasks import TaskSnapshot

MAX_HUMAN_REVIEW_SUBMISSION_BYTES = 1_000_000


class FindingReview(ContractModel):
    finding_id: UUID
    action: Literal["confirm", "reject", "relabel"]
    label: DetectionLabel | Literal["OTHER"] | None = None
    open_label: NonEmptyStr | None = None
    reason: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_action_fields(self) -> "FindingReview":
        if self.action in {"confirm", "reject"} and self.label is not None:
            raise ValueError("confirm and reject cannot propose a replacement label")
        if self.action == "reject" and self.open_label is not None:
            raise ValueError("reject cannot propose an open label")
        if self.action == "relabel":
            if self.label is None:
                raise ValueError("relabel requires a label")
            if self.label == "OTHER" and self.open_label is None:
                raise ValueError("OTHER relabel requires an open label")
            if self.label != "OTHER" and self.open_label is not None:
                raise ValueError("open label is only valid for OTHER")
        return self


class HumanReviewSubmission(ContractModel):
    request_id: UUID
    reviewer: str = Field(min_length=1, max_length=100)
    snapshot_updated_at: AwareDatetime
    decisions: tuple[FindingReview, ...] = Field(max_length=2000)
    negative_audit_complete: bool = False
    note: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def validate_complete_request_identity(self) -> "HumanReviewSubmission":
        ids = [decision.finding_id for decision in self.decisions]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate finding decisions")
        encoded = json.dumps(
            # Match the default Temporal JSON converter, including Unicode escapes.
            self.model_dump(mode="json"),
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > MAX_HUMAN_REVIEW_SUBMISSION_BYTES:
            raise ValueError("encoded submission exceeds 1000000 bytes")
        return self


class HumanReviewRecord(ContractModel):
    task_id: UUID
    submission: HumanReviewSubmission
    before: TaskSnapshot
    after: TaskSnapshot
    reviewed_at: AwareDatetime

    @model_validator(mode="after")
    def validate_task_identity(self) -> "HumanReviewRecord":
        if self.before.task_id != self.task_id or self.after.task_id != self.task_id:
            raise ValueError("review snapshots must belong to the reviewed task")
        return self


class HumanReviewDetail(ContractModel):
    task_id: UUID
    can_review: bool
    snapshot: TaskSnapshot
    record: HumanReviewRecord | None = None

    @model_validator(mode="after")
    def validate_task_identity(self) -> "HumanReviewDetail":
        if self.snapshot.task_id != self.task_id:
            raise ValueError("review detail snapshot must belong to the task")
        if self.record is not None and self.record.task_id != self.task_id:
            raise ValueError("review detail record must belong to the task")
        return self
