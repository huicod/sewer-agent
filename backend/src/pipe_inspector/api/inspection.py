"""Inspection execution and tool audit endpoints."""

from uuid import UUID

from fastapi import APIRouter

from pipe_inspector.contracts.intake import TaskRecord
from pipe_inspector.contracts.tools import ToolCall


def inspection_router(repository, starter) -> APIRouter:
    router = APIRouter(prefix="/api/tasks", tags=["inspection"])

    @router.post("/{task_id}/start", response_model=TaskRecord, status_code=202)
    async def start_image(task_id: UUID):
        return await starter.start(task_id)

    @router.get("/{task_id}/calls", response_model=tuple[ToolCall, ...])
    def tool_calls(task_id: UUID):
        repository.get_task(task_id)
        return repository.list_calls(task_id)

    return router
