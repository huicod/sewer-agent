"""Conversation intake records; execution state remains a server-owned projection."""

from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from .assets import AssetRef
from .common import ContractModel
from .tasks import TaskSnapshot


class SessionRecord(ContractModel):
    session_id: UUID
    created_at: AwareDatetime


class TaskSubmission(ContractModel):
    request_id: UUID
    session_id: UUID
    input_asset: AssetRef
    instruction: str = Field(min_length=1, max_length=4000)


class TaskRecord(ContractModel):
    request_id: UUID
    session_id: UUID
    input_asset: AssetRef
    instruction: str = Field(min_length=1, max_length=4000)
    created_at: AwareDatetime
    snapshot: TaskSnapshot
    parent_task_id: UUID | None = None

    @model_validator(mode="after")
    def validate_parent_link(self) -> "TaskRecord":
        if self.parent_task_id == self.snapshot.task_id:
            raise ValueError("task cannot be its own parent")
        return self


class SessionDetail(ContractModel):
    session: SessionRecord
    tasks: tuple[TaskRecord, ...] = ()
