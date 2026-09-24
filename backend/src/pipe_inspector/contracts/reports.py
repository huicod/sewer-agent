"""Evidence exports never let a language model replace the stored conclusion."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, model_validator

from .assets import AssetRef
from .common import ContractModel
from .enhancement import EnhancementResult, EnhancementReview, EnhancementSelection
from .geometry import GeometryDetail
from .image_evidence import ImageEvidence
from .intake import SessionRecord, TaskRecord
from .tools import ToolCall
from .turns import TurnRecord


class TaskReport(ContractModel):
    schema_version: Literal["1"] = "1"
    task: TaskRecord
    tool_calls: tuple[ToolCall, ...] = ()
    enhancement_selection: EnhancementSelection | None = None
    enhancement: EnhancementResult | None = None
    enhancement_review: EnhancementReview | None = None
    geometry: GeometryDetail | None = None
    image_evidence: ImageEvidence | None = None

    @model_validator(mode="after")
    def validate_source(self):
        if not self.task.snapshot.is_terminal:
            raise ValueError("reports require a terminal snapshot")
        if any(call.task_id != self.task.snapshot.task_id for call in self.tool_calls):
            raise ValueError("report calls must belong to the reported task")
        if self.image_evidence is not None and (
            self.image_evidence.task_id != self.task.snapshot.task_id
            or self.image_evidence.input_asset != self.task.input_asset
            or self.image_evidence.findings != self.task.snapshot.findings
        ):
            raise ValueError("report image evidence must match the reported task")
        if self.enhancement is not None and self.enhancement.input_asset != self.task.input_asset:
            raise ValueError("enhancement input must belong to the reported task")
        if self.enhancement_review is not None and self.enhancement is None:
            raise ValueError("enhancement review requires enhancement result")
        if self.geometry is not None:
            if self.geometry.task_id != self.task.snapshot.task_id:
                raise ValueError("report geometry must belong to the reported task")
            values = (
                self.geometry.segmentation,
                self.geometry.depth,
                self.geometry.measurements,
            )
            if any(
                value is not None and value.input_asset != self.task.input_asset
                for value in values
            ):
                raise ValueError("report geometry source must belong to the reported task")
        return self


class SessionReportSource(ContractModel):
    task_id: UUID
    root_task_id: UUID
    role: Literal["primary", "supplement", "video_frame_tool"]
    task: TaskRecord
    tool_calls: tuple[ToolCall, ...] = ()
    enhancement_selection: EnhancementSelection | None = None
    enhancement: EnhancementResult | None = None
    enhancement_review: EnhancementReview | None = None
    geometry: GeometryDetail | None = None
    image_evidence: ImageEvidence | None = None

    @model_validator(mode="after")
    def validate_source(self):
        if self.task_id != self.task.snapshot.task_id:
            raise ValueError("session report source task ID must match its task")
        if any(call.task_id != self.task_id for call in self.tool_calls):
            raise ValueError("session report calls must belong to their source task")
        return self


class SessionReportAssetGroup(ContractModel):
    input_asset: AssetRef
    sources: tuple[SessionReportSource, ...]


class SessionReport(ContractModel):
    schema_version: Literal["1"] = "1"
    session: SessionRecord
    generated_at: AwareDatetime
    is_final: bool
    turns: tuple[TurnRecord, ...] = ()
    asset_groups: tuple[SessionReportAssetGroup, ...] = ()

    @model_validator(mode="after")
    def validate_session_bindings(self):
        session_id = self.session.session_id
        if any(turn.session_id != session_id for turn in self.turns):
            raise ValueError("session report turns must belong to the session")
        for group in self.asset_groups:
            for source in group.sources:
                if source.task.session_id != session_id:
                    raise ValueError("session report tasks must belong to the session")
                if source.task.input_asset != group.input_asset:
                    raise ValueError("session report asset groups must match their source tasks")
        return self
