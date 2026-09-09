"""Constrained task-planning contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from pipe_inspector.contracts.common import ContractModel, NonEmptyStr
from pipe_inspector.contracts.detection import DetectionLabel


class ActionKind(StrEnum):
    DETECT = "detect"
    VERIFY = "verify"
    OPEN_SCAN = "open_scan"
    ENHANCE = "enhance"
    SEGMENT = "segment"
    RELATIVE_MEASURE = "relative_measure"
    COMPARE = "compare"
    RETRIEVE_STANDARD = "retrieve_standard"
    SUMMARIZE = "summarize"
    EXPORT_REPORT = "export_report"


class OutputFormat(StrEnum):
    JSON = "json"
    HTML = "html"


class CallBudget(ContractModel):
    max_tool_calls: int = Field(default=32, ge=1, le=128)
    max_transport_attempts: int = Field(default=3, ge=1, le=5)
    per_event_supplement: int = Field(default=1, ge=0, le=1)
    per_event_open_scan: int = Field(default=1, ge=0, le=1)
    per_event_verifications: int = Field(default=2, ge=0, le=2)


class PlanStep(ContractModel):
    step_id: NonEmptyStr
    action: ActionKind
    depends_on: tuple[NonEmptyStr, ...] = ()
    required: bool = True


class TaskPlan(ContractModel):
    schema_version: Literal["1"] = "1"
    target_labels: tuple[DetectionLabel, ...] = tuple(DetectionLabel)
    open_anomaly_audit: bool = False
    steps: tuple[PlanStep, ...]
    outputs: tuple[OutputFormat, ...] = (OutputFormat.JSON, OutputFormat.HTML)
    budget: CallBudget = Field(default_factory=CallBudget)

    @model_validator(mode="after")
    def validate_plan(self) -> "TaskPlan":
        if not self.target_labels or len(set(self.target_labels)) != len(self.target_labels):
            raise ValueError("target_labels must be nonempty and unique")
        if not self.outputs or len(set(self.outputs)) != len(self.outputs):
            raise ValueError("outputs must be nonempty and unique")
        if len(self.steps) > self.budget.max_tool_calls:
            raise ValueError("steps exceed max_tool_calls")

        step_ids = [step.step_id for step in self.steps]
        if len(set(step_ids)) != len(step_ids):
            raise ValueError("step IDs must be unique")
        detect_positions = [
            index for index, step in enumerate(self.steps) if step.action is ActionKind.DETECT
        ]
        if detect_positions != [0]:
            raise ValueError("detect must appear exactly once at position zero")

        detect = self.steps[0]
        if not detect.required or detect.depends_on:
            raise ValueError("detect must be required and dependency-free")

        detect_id = detect.step_id
        prior_steps: dict[str, PlanStep] = {}
        ancestors: dict[str, set[str]] = {}
        for index, step in enumerate(self.steps):
            step_id = str(step.step_id)
            dependencies = [str(dependency) for dependency in step.depends_on]
            if len(set(dependencies)) != len(dependencies):
                raise ValueError("step dependencies must be unique")
            if any(dependency not in prior_steps for dependency in dependencies):
                raise ValueError("dependencies must refer only to earlier steps")

            step_ancestors = set(dependencies)
            for dependency in dependencies:
                step_ancestors.update(ancestors[dependency])
            if index and str(detect_id) not in step_ancestors:
                raise ValueError("every non-detect step must depend on detect")
            if step.action is ActionKind.RELATIVE_MEASURE and not any(
                prior_steps[ancestor].action is ActionKind.SEGMENT for ancestor in step_ancestors
            ):
                raise ValueError("relative_measure requires a segment ancestor")

            prior_steps[step_id] = step
            ancestors[step_id] = step_ancestors
        return self
