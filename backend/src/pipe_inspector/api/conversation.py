"""Conversation admission and saved progress; no model execution in HTTP handlers."""

from uuid import UUID

from fastapi import APIRouter

from pipe_inspector.contracts.turns import TurnInput, TurnRecord
from pipe_inspector.storage.repository import MissingRecord


def conversation_router(repository, starter) -> APIRouter:
    router = APIRouter(prefix="/api/sessions/{session_id}/turns", tags=["conversation"])

    @router.post("", response_model=TurnRecord, status_code=202)
    async def submit_turn(session_id: UUID, body: TurnInput):
        return await starter.submit(session_id, body)

    @router.get("", response_model=tuple[TurnRecord, ...])
    def list_turns(session_id: UUID):
        return repository.list_turns(session_id)

    @router.get("/{request_id}", response_model=TurnRecord)
    def get_turn(session_id: UUID, request_id: UUID):
        turn = repository.get_turn(request_id)
        if turn.session_id != session_id:
            raise MissingRecord
        return turn

    return router
