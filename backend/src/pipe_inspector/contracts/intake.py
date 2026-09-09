"""Conversation intake records; execution state remains a server-owned projection."""

from uuid import UUID

from pydantic import AwareDatetime, Field

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


class SessionDetail(ContractModel):
    session: SessionRecord
    tasks: tuple[TaskRecord, ...] = ()
