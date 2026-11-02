"""Media/session/task HTTP boundary. T04 admits work; it does not execute it."""

from pathlib import Path
from typing import Annotated, BinaryIO
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi import Path as PathParam
from fastapi.responses import FileResponse
from pydantic import TypeAdapter, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException

from pipe_inspector.contracts.assets import AssetRecord, AssetRef
from pipe_inspector.contracts.intake import (
    ExampleVideo,
    SessionDetail,
    SessionRecord,
    SessionSummary,
    SessionUpdate,
    TaskRecord,
    TaskSubmission,
)
from pipe_inspector.media import MediaError, MediaStore
from pipe_inspector.storage.repository import PostgresRepository


def _example_videos(root: Path) -> list[ExampleVideo]:
    manifest = root / "field-videos.json"
    if manifest.is_symlink():
        raise MediaError("example_catalog_invalid", "现场视频案例目录无效", 503)
    try:
        videos = TypeAdapter(list[ExampleVideo]).validate_json(manifest.read_bytes())
    except FileNotFoundError:
        return []
    except ValidationError:
        raise MediaError("example_catalog_invalid", "现场视频案例目录无效", 503) from None
    if len({video.id for video in videos}) != len(videos):
        raise MediaError("example_catalog_invalid", "现场视频案例编号重复", 503)
    return videos


def intake_router(repo: PostgresRepository, media: MediaStore, example_root: Path) -> APIRouter:
    router = APIRouter(prefix="/api")
    example_root = example_root.absolute()

    def save_asset(stream: BinaryIO) -> AssetRecord:
        record = media.ingest(stream)
        # Keep bytes on ambiguous DB commit failure; evidence may already be committed.
        repo.save_asset(record)
        return record

    @router.get("/example-videos", response_model=list[ExampleVideo])
    def list_example_videos():
        return _example_videos(example_root)

    @router.post("/example-videos/{id}/asset", response_model=AssetRecord, status_code=201)
    def import_example_video(id: str):
        if not any(video.id == id for video in _example_videos(example_root)):
            raise MediaError("example_not_found", "未找到现场视频案例", 404)
        clip = example_root / id / "clip.mp4"
        if clip.parent.is_symlink() or clip.is_symlink() or not clip.is_file():
            raise MediaError("example_unavailable", "现场视频案例文件不可用", 404)
        with clip.open("rb") as stream:
            return save_asset(stream)

    @router.post("/sessions", response_model=SessionRecord, status_code=201)
    def create_session():
        return repo.create_session()

    @router.get("/sessions", response_model=tuple[SessionSummary, ...])
    def list_sessions():
        return repo.list_sessions()

    @router.get("/sessions/{session_id}", response_model=SessionDetail)
    def get_session(session_id: UUID):
        return repo.get_session(session_id)

    @router.patch("/sessions/{session_id}", response_model=SessionSummary)
    def update_session(session_id: UUID, update: SessionUpdate):
        return repo.update_session(session_id, update)

    @router.post("/assets", response_model=AssetRecord, status_code=201)
    async def upload(request: Request):
        async with request.form(max_files=1, max_fields=0) as form:
            file = form.get("file")
            if not isinstance(file, UploadFile):
                raise HTTPException(422, detail={"code": "missing_file", "message": "请选择文件"})

            return await run_in_threadpool(save_asset, file.file)

    @router.get("/assets/{asset_id}/versions/{version}", response_model=AssetRecord)
    def get_asset(asset_id: UUID, version: Annotated[int, PathParam(ge=1)]):
        return repo.get_asset(AssetRef(asset_id=asset_id, version=version))

    @router.get("/assets/{asset_id}/versions/{version}/content")
    def get_content(asset_id: UUID, version: Annotated[int, PathParam(ge=1)]):
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
