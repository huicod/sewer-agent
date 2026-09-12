"""Read-only JSON/HTML exports of terminal inspection evidence."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from pipe_inspector.api.inspection import load_enhancement_detail
from pipe_inspector.contracts.reports import TaskReport
from pipe_inspector.reports import render_html


def report_router(repository) -> APIRouter:
    router = APIRouter(prefix="/api/tasks", tags=["reports"])

    @router.get("/{task_id}/report", response_model=None)
    def get_report(task_id: UUID, format: Literal["json", "html"] = "json"):
        task = repository.get_task(task_id)
        if not task.snapshot.is_terminal:
            raise HTTPException(
                409,
                detail={
                    "code": "report_not_ready",
                    "message": "任务尚未结束，请先查看实时进度",
                },
            )
        enhancement = load_enhancement_detail(repository, task)
        report = TaskReport(
            task=task,
            tool_calls=repository.list_calls(task_id),
            enhancement_selection=enhancement.selection,
            enhancement=enhancement.result,
            enhancement_review=enhancement.review,
        )
        headers = {
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
            "Content-Disposition": f'inline; filename="inspection-{task_id}.{format}"',
        }
        if format == "html":
            headers["Content-Security-Policy"] = (
                "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; base-uri 'none'; "
                "form-action 'none'; frame-ancestors 'none'"
            )
            return HTMLResponse(render_html(report), headers=headers)
        return JSONResponse(report.model_dump(mode="json"), headers=headers)

    return router
