"""Constrained contracts for conversation intent parsing."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from pipe_inspector.contracts.common import ContractModel
from pipe_inspector.contracts.detection import DetectionLabel
from pipe_inspector.contracts.enhancement import EnhancementOptions
from pipe_inspector.contracts.planning import ActionKind, OutputFormat


class ConversationIntentKind(StrEnum):
    INSPECT = "inspect"
    ASK = "ask"
    REPORT = "report"
    SUPPLEMENT = "supplement"
    CLARIFY = "clarify"
    REFUSE = "refuse"


class ConversationMessage(ContractModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class ConversationInput(ContractModel):
    text: str = Field(min_length=1, max_length=4000)
    history: tuple[ConversationMessage, ...] = Field(default=(), max_length=10)


class ConversationIntent(ContractModel):
    kind: ConversationIntentKind
    target_labels: tuple[DetectionLabel, ...] = tuple(DetectionLabel)
    actions: tuple[ActionKind, ...] = Field(default=(), max_length=8)
    outputs: tuple[OutputFormat, ...] = (OutputFormat.JSON, OutputFormat.HTML)
    message: str = Field(min_length=1, max_length=2000)
    enhancement: EnhancementOptions | None = None
    auto_enhancement: bool = False

    @model_validator(mode="after")
    def validate_intent(self) -> "ConversationIntent":
        if self.auto_enhancement and (
            self.enhancement is not None or ActionKind.ENHANCE not in self.actions
        ):
            raise ValueError("automatic enhancement requires enhance and no explicit options")
        if self.enhancement is not None and ActionKind.ENHANCE not in self.actions:
            raise ValueError("enhancement options require the enhance action")
        if not self.target_labels or len(set(self.target_labels)) != len(self.target_labels):
            raise ValueError("target_labels must be nonempty and unique")
        if len(set(self.actions)) != len(self.actions):
            raise ValueError("actions must be unique")
        if not self.outputs or len(set(self.outputs)) != len(self.outputs):
            raise ValueError("outputs must be nonempty and unique")

        no_action_kinds = {
            ConversationIntentKind.ASK,
            ConversationIntentKind.REPORT,
            ConversationIntentKind.CLARIFY,
            ConversationIntentKind.REFUSE,
        }
        if self.kind in no_action_kinds and self.actions:
            raise ValueError(f"{self.kind.value} intent cannot contain actions")
        if self.kind is ConversationIntentKind.SUPPLEMENT:
            if not self.actions:
                raise ValueError("supplement intent requires actions")
            if ActionKind.DETECT in self.actions:
                raise ValueError("supplement intent cannot request detect")
        if self.kind is ConversationIntentKind.INSPECT and ActionKind.DETECT in self.actions:
            raise ValueError("inspect actions contain only requests in addition to detect")
        return self
