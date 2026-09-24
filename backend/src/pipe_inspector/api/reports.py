"""Read-only JSON/HTML exports of terminal inspection evidence."""

from collections import OrderedDict
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from pipe_inspector.api.image_evidence import load_image_evidence
from pipe_inspector.api.inspection import load_enhancement_detail, load_geometry_detail
from pipe_inspector.contracts.planning import ActionKind
from pipe_inspector.contracts.reports import (
    SessionReport,
    SessionReportAssetGroup,
    SessionReportSource,
    TaskReport,
)
from pipe_inspector.reports import render_html
from pipe_inspector.session_report import render_session_html


def _load_task_evidence(repository, task):
    enhancement = load_enhancement_detail(repository, task)
    calls = repository.list_calls(task.snapshot.task_id)
    geometry_actions = {
        ActionKind.SEGMENT,
        ActionKind.RELATIVE_DEPTH,
        ActionKind.RELATIVE_MEASURE,
    }
    geometry = (
        load_geometry_detail(repository, task)
        if any(call.tool_name in geometry_actions for call in calls)
        else None
    )
    image_evidence = load_image_evidence(repository, task)
    if image_evidence.observation is None and image_evidence.review is None:
        image_evidence = None
    return {
        "tool_calls": calls,
        "enhancement_selection": enhancement.selection,
        "enhancement": enhancement.result,
        "enhancement_review": enhancement.review,
        "geometry": geometry,
        "image_evidence": image_evidence,
    }


def build_session_report(repository, session_id: UUID) -> SessionReport:
    detail = repository.get_session(session_id)
    tasks = detail.tasks
    by_id = {task.snapshot.task_id: task for task in tasks}

    def root_id(task):
        seen = {task.snapshot.task_id}
        current = task
        while current.parent_task_id is not None:
            if current.parent_task_id in seen or current.parent_task_id not in by_id:
                raise ValueError("session task parent lineage is invalid")
            seen.add(current.parent_task_id)
            current = by_id[current.parent_task_id]
        return current.snapshot.task_id

    groups = OrderedDict()
    for task in tasks:
        role = (
            "video_frame_tool"
            if task.frame_source is not None
            else "supplement"
            if task.parent_task_id is not None
            else "primary"
        )
        source = SessionReportSource(
            task_id=task.snapshot.task_id,
            root_task_id=root_id(task),
            role=role,
            task=task,
            **_load_task_evidence(repository, task),
        )
        groups.setdefault(task.input_asset, []).append(source)
    turns = repository.list_turns(session_id, limit=None)
    final_turn_statuses = {"completed", "failed"}
    return SessionReport(
        session=detail.session,
        generated_at=datetime.now(timezone.utc),
        is_final=all(task.snapshot.is_terminal for task in tasks)
        and all(turn.status in final_turn_statuses for turn in turns),
        turns=turns,
        asset_groups=tuple(
            SessionReportAssetGroup(input_asset=asset, sources=tuple(sources))
            for asset, sources in groups.items()
        ),
    )


def report_router(repository) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["reports"])

    @router.get("/sessions/{session_id}/report", response_model=None)
    def get_session_report(
        session_id: UUID, format: Literal["json", "html"] = "json"
    ):
        report = build_session_report(repository, session_id)
        headers = {
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
            "Content-Disposition": f'inline; filename="conversation-{session_id}.{format}"',
        }
        if format == "html":
            headers["Content-Security-Policy"] = (
                "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; base-uri 'none'; "
                "form-action 'none'; frame-ancestors 'none'"
            )
            return HTMLResponse(render_session_html(report), headers=headers)
        return JSONResponse(report.model_dump(mode="json"), headers=headers)

    @router.get("/tasks/{task_id}/report", response_model=None)
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
        report = TaskReport(task=task, **_load_task_evidence(repository, task))
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
