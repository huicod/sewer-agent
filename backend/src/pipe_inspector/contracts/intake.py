"""Conversation intake records; execution state remains a server-owned projection."""

from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from .assets import AssetRef
from .common import ContractModel
from .tasks import InspectionMode, TaskSnapshot


class FrameSource(ContractModel):
    video_asset: AssetRef
    frame_index: int = Field(ge=0)
    timestamp_seconds: float = Field(ge=0)


class SessionRecord(ContractModel):
    session_id: UUID
    created_at: AwareDatetime


class SessionSummary(SessionRecord):
    title: str = Field(min_length=1, max_length=120)
    updated_at: AwareDatetime
    task_count: int = Field(ge=0)
    turn_count: int = Field(ge=0)


class TaskSubmission(ContractModel):
    request_id: UUID
    session_id: UUID
    input_asset: AssetRef
    instruction: str = Field(min_length=1, max_length=4000)
    inspection_mode: InspectionMode = "yolo_vlm"


class TaskRecord(ContractModel):
    request_id: UUID
    session_id: UUID
    input_asset: AssetRef
    instruction: str = Field(min_length=1, max_length=4000)
    inspection_mode: InspectionMode = "yolo_vlm"
    created_at: AwareDatetime
    snapshot: TaskSnapshot
    parent_task_id: UUID | None = None
    frame_source: FrameSource | None = None

    @model_validator(mode="after")
    def validate_parent_link(self) -> "TaskRecord":
        if self.parent_task_id == self.snapshot.task_id:
            raise ValueError("task cannot be its own parent")
        if self.frame_source is not None and self.parent_task_id is None:
            raise ValueError("frame source requires parent task")
        return self


class SessionDetail(ContractModel):
    session: SessionRecord
    tasks: tuple[TaskRecord, ...] = ()
