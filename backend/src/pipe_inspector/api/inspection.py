"""Inspection execution and tool audit endpoints."""

from uuid import UUID

from fastapi import APIRouter

from pipe_inspector.contracts.assets import AssetKind
from pipe_inspector.contracts.enhancement import (
    EnhancementDetail,
    EnhancementResult,
    EnhancementReview,
    EnhancementSelection,
)
from pipe_inspector.contracts.intake import TaskRecord
from pipe_inspector.contracts.tools import ToolCall
from pipe_inspector.workflow.start import InspectionStartError


def load_enhancement_detail(repository, task: TaskRecord) -> EnhancementDetail:
    """Validate the public enhancement checkpoints against their owning task."""
    task_id = task.snapshot.task_id
    raw_result = repository.load_step(task_id, "enhance")
    raw_review = repository.load_step(task_id, "enhancement_review")
    raw_selection = repository.load_step(task_id, "enhancement_selection")
    result = EnhancementResult.model_validate(raw_result) if raw_result is not None else None
    review = EnhancementReview.model_validate(raw_review) if raw_review is not None else None
    selection = None
    if raw_selection is not None:
        if not isinstance(raw_selection, dict) or set(raw_selection) != {"selection"}:
            raise ValueError("invalid enhancement selection checkpoint")
        selection = EnhancementSelection.model_validate(raw_selection["selection"])
    if result is not None and result.input_asset != task.input_asset:
        raise ValueError("enhancement input does not belong to requested task")
    if review is not None and result is None:
        raise ValueError("enhancement review requires enhancement result")
    return EnhancementDetail(task_id=task_id, result=result, review=review, selection=selection)


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

    @router.get("/{task_id}/enhancement", response_model=EnhancementDetail)
    def enhancement_detail(task_id: UUID):
        task = repository.get_task(task_id)
        return load_enhancement_detail(repository, task)

    @router.get("/{task_id}/calls", response_model=tuple[ToolCall, ...])
    def tool_calls(task_id: UUID):
        repository.get_task(task_id)
        return repository.list_calls(task_id)

    return router
