"""Media/session/task HTTP boundary. T04 admits work; it does not execute it."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Request
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException

from pipe_inspector.contracts.assets import AssetRecord, AssetRef
from pipe_inspector.contracts.intake import (
    SessionDetail,
    SessionRecord,
    SessionSummary,
    TaskRecord,
    TaskSubmission,
)
from pipe_inspector.media import MediaError, MediaStore
from pipe_inspector.storage.repository import PostgresRepository


def intake_router(repo: PostgresRepository, media: MediaStore) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.post("/sessions", response_model=SessionRecord, status_code=201)
    def create_session():
        return repo.create_session()

    @router.get("/sessions", response_model=tuple[SessionSummary, ...])
    def list_sessions():
        return repo.list_sessions()

    @router.get("/sessions/{session_id}", response_model=SessionDetail)
    def get_session(session_id: UUID):
        return repo.get_session(session_id)

    @router.post("/assets", response_model=AssetRecord, status_code=201)
    async def upload(request: Request):
        async with request.form(max_files=1, max_fields=0) as form:
            file = form.get("file")
            if not isinstance(file, UploadFile):
                raise HTTPException(422, detail={"code": "missing_file", "message": "请选择文件"})

            def save():
                record = media.ingest(file.file)
                # Keep bytes on ambiguous DB commit failure; evidence may already be committed.
                repo.save_asset(record)
                return record

            return await run_in_threadpool(save)

    @router.get("/assets/{asset_id}/versions/{version}", response_model=AssetRecord)
    def get_asset(asset_id: UUID, version: Annotated[int, Path(ge=1)]):
        return repo.get_asset(AssetRef(asset_id=asset_id, version=version))

    @router.get("/assets/{asset_id}/versions/{version}/content")
    def get_content(asset_id: UUID, version: Annotated[int, Path(ge=1)]):
        record = repo.get_asset(AssetRef(asset_id=asset_id, version=version))
        path = media.path_for(record.ref)
        if not path.is_file():
            raise MediaError("asset_unavailable", "原始文件不可用", 404)
        return FileResponse(
            path,
            media_type=record.media_type,
            headers={
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.post("/tasks", response_model=TaskRecord, status_code=201)
    def create_task(submission: TaskSubmission):
        return repo.create_task(submission)

    @router.get("/tasks/{task_id}", response_model=TaskRecord)
    def get_task(task_id: UUID):
        return repo.get_task(task_id)

    return router
