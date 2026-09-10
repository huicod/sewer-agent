"""Inspection execution and tool audit endpoints."""

from uuid import UUID

from fastapi import APIRouter

from pipe_inspector.contracts.assets import AssetKind
from pipe_inspector.contracts.intake import TaskRecord
from pipe_inspector.contracts.tools import ToolCall
from pipe_inspector.workflow.start import InspectionStartError


def inspection_router(repository, starter) -> APIRouter:
    router = APIRouter(prefix="/api/tasks", tags=["inspection"])

    @router.post("/{task_id}/start", response_model=TaskRecord, status_code=202)
    async def start_inspection(task_id: UUID):
        return await starter.start(task_id)

    @router.post("/{task_id}/cancel", response_model=TaskRecord, status_code=202)
    async def cancel_video(task_id: UUID):
        return await starter.cancel(task_id)

    @router.get("/{task_id}/video")
    def video_detail(task_id: UUID):
        task = repository.get_task(task_id)
        asset = repository.get_asset(task.input_asset)
        if asset.kind != AssetKind.VIDEO:
            raise InspectionStartError("video_only", "只有视频任务具有视频检查详情", 409)
        return {
            "task_id": task_id,
            "scan": repository.load_step(task_id, "video_scan"),
            "evidence": repository.load_step(task_id, "video_evidence"),
        }

    @router.get("/{task_id}/calls", response_model=tuple[ToolCall, ...])
    def tool_calls(task_id: UUID):
        repository.get_task(task_id)
        return repository.list_calls(task_id)

    return router
