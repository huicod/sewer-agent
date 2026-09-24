"""Conversation admission and saved progress; no model execution in HTTP handlers."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from pipe_inspector.comparison_report import render_comparison_html
from pipe_inspector.contracts.comparison import ComparisonResult
from pipe_inspector.contracts.turns import TurnInput, TurnRecord
from pipe_inspector.storage.repository import MissingRecord


def load_comparison_result(
    repository, session_id: UUID, request_id: UUID
) -> ComparisonResult | None:
    """Load one immutable comparison while rechecking its conversation bindings."""
    turn = repository.get_turn(request_id)
    if turn.session_id != session_id:
        raise MissingRecord
    stored = repository.load_turn_step(request_id, "comparison")
    if stored is None:
        return None
    result = ComparisonResult.model_validate(stored)
    reference_id = result.reference.snapshot.task_id
    current_id = result.current.snapshot.task_id
    if (
        result.request_id != request_id
        or result.reference.session_id != session_id
        or result.current.session_id != session_id
        or turn.input_asset is not None
        or (turn.reference_task_id is not None and turn.reference_task_id != reference_id)
        or (turn.linked_task_id is not None and turn.linked_task_id != current_id)
        or (turn.task_id is not None and turn.task_id != current_id)
    ):
        raise ValueError("saved comparison does not match the requested turn and sources")
    return result


def conversation_router(repository, starter) -> APIRouter:
    router = APIRouter(prefix="/api/sessions/{session_id}/turns", tags=["conversation"])

    @router.post("", response_model=TurnRecord, status_code=202)
    async def submit_turn(session_id: UUID, body: TurnInput):
        return await starter.submit(session_id, body)

    @router.get("", response_model=tuple[TurnRecord, ...])
    def list_turns(session_id: UUID):
        return repository.list_turns(session_id, limit=None)

    @router.get("/{request_id}", response_model=TurnRecord)
    def get_turn(session_id: UUID, request_id: UUID):
        turn = repository.get_turn(request_id)
        if turn.session_id != session_id:
            raise MissingRecord
        return turn

    @router.get("/{request_id}/comparison", response_model=ComparisonResult | None)
    def get_comparison(session_id: UUID, request_id: UUID):
        return load_comparison_result(repository, session_id, request_id)

    @router.get("/{request_id}/comparison/report", response_model=None)
    def get_comparison_report(
        session_id: UUID,
        request_id: UUID,
        format: Literal["json", "html"] = "json",
    ):
        result = load_comparison_result(repository, session_id, request_id)
        if result is None:
            raise HTTPException(
                409,
                detail={
                    "code": "comparison_not_ready",
                    "message": "该对话尚无已保存的对比结果",
                },
            )
        headers = {
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
            "Content-Disposition": f'inline; filename="comparison-{request_id}.{format}"',
        }
        if format == "html":
            headers["Content-Security-Policy"] = (
                "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; "
                "form-action 'none'; frame-ancestors 'none'"
            )
            return HTMLResponse(render_comparison_html(result), headers=headers)
        return JSONResponse(result.model_dump(mode="json"), headers=headers)

    return router
