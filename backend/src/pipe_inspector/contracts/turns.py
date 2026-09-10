"""Public contracts for durable conversation turns."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from .assets import AssetRef
from .common import ContractModel

TurnStatus = Literal["queued", "running", "completed", "failed"]
TurnStage = Literal["queued", "planning", "executing", "finished"]


class TurnInput(ContractModel):
    request_id: UUID
    text: str = Field(min_length=1, max_length=4000)
    input_asset: AssetRef | None = None
    task_id: UUID | None = None


class TurnSubmission(TurnInput):
    session_id: UUID


class TurnRecord(TurnSubmission):
    created_at: AwareDatetime
    updated_at: AwareDatetime
    status: TurnStatus = "queued"
    stage: TurnStage = "queued"
    reply: str | None = Field(default=None, max_length=4000)
    linked_task_id: UUID | None = None
    error_code: str | None = Field(default=None, max_length=100)
    attempts: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_state(self) -> "TurnRecord":
        if self.status == "completed":
            if not self.reply or self.stage != "finished":
                raise ValueError("completed turns require a reply and finished stage")
        elif self.status == "failed":
            if not self.error_code or self.stage != "finished":
                raise ValueError("failed turns require an error code and finished stage")
        elif self.error_code is not None:
            raise ValueError("nonterminal turns cannot have an error code")
        return self
