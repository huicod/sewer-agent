"""Versioned tool-call records without execution or retry behavior."""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import Field, model_validator

from pipe_inspector.contracts.assets import AssetRef
from pipe_inspector.contracts.common import ContractModel, NonEmptyStr
from pipe_inspector.contracts.planning import ActionKind


class ToolResource(StrEnum):
    CPU = "cpu"
    GPU = "gpu"
    API = "api"


class ToolCallStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    UNCERTAIN = "uncertain"


class ToolError(ContractModel):
    code: NonEmptyStr
    retryable: bool
    message: NonEmptyStr


class ToolCall(ContractModel):
    """One logical call whose idempotency key stays stable across transport retries."""

    call_id: UUID
    task_id: UUID
    step_id: NonEmptyStr
    idempotency_key: NonEmptyStr
    attempt: int = Field(default=1, ge=1)
    tool_name: ActionKind
    tool_version: NonEmptyStr
    model_version: NonEmptyStr | None = None
    resource: ToolResource
    timeout_seconds: float = Field(gt=0, allow_inf_nan=False)
    input_assets: tuple[AssetRef, ...]
    output_assets: tuple[AssetRef, ...] = ()
    status: ToolCallStatus = ToolCallStatus.QUEUED
    error: ToolError | None = None
    skip_reason: NonEmptyStr | None = None

    @model_validator(mode="after")
    def validate_status_details(self) -> "ToolCall":
        if self.status in {ToolCallStatus.FAILED, ToolCallStatus.UNCERTAIN}:
            if self.error is None:
                raise ValueError("failed and uncertain calls require an error")
        elif self.error is not None:
            raise ValueError("this status must not carry an error")

        if self.status is ToolCallStatus.SKIPPED:
            if self.skip_reason is None:
                raise ValueError("skipped calls require a skip reason")
        elif self.skip_reason is not None:
            raise ValueError("only skipped calls may carry a skip reason")
        return self
