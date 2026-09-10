"""Evidence exports never let a language model replace the stored conclusion."""

from typing import Literal

from pydantic import model_validator

from .common import ContractModel
from .intake import TaskRecord
from .tools import ToolCall


class TaskReport(ContractModel):
    schema_version: Literal["1"] = "1"
    task: TaskRecord
    tool_calls: tuple[ToolCall, ...] = ()

    @model_validator(mode="after")
    def validate_source(self):
        if not self.task.snapshot.is_terminal:
            raise ValueError("reports require a terminal snapshot")
        if any(call.task_id != self.task.snapshot.task_id for call in self.tool_calls):
            raise ValueError("report calls must belong to the reported task")
        return self
