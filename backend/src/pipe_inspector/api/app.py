"""Application factory: no GPU or remote connection during import."""

import psycopg
from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from pipe_inspector.api.inspection import inspection_router
from pipe_inspector.api.intake import intake_router
from pipe_inspector.api.limits import UPLOAD_LIMIT_DETAIL, BodyLimitMiddleware
from pipe_inspector.config import Settings
from pipe_inspector.media import MediaError, MediaStore
from pipe_inspector.storage.inspection import InspectionRepository
from pipe_inspector.storage.repository import MissingRecord, PostgresRepository, SubmissionConflict
from pipe_inspector.workflow.start import InspectionStarter, InspectionStartError


def create_app(
    settings: Settings | None = None, *, repository: PostgresRepository | None = None, starter=None
):
    settings = settings or Settings()
    repo = repository or InspectionRepository(settings.database_dsn)
    app = FastAPI(title="Sewer Agent", version="0.1.0")
    app.add_middleware(BodyLimitMiddleware, max_upload_bytes=settings.max_upload_bytes)
    media = MediaStore(
        settings.asset_root,
        max_upload_bytes=settings.max_upload_bytes,
        max_pixels=settings.max_media_pixels,
        probe_timeout_seconds=settings.media_probe_timeout_seconds,
    )
    app.include_router(intake_router(repo, media))
    app.include_router(inspection_router(repo, starter or InspectionStarter(settings, repo)))

    @app.exception_handler(InspectionStartError)
    async def start_error(request: Request, exc: InspectionStartError):
        return JSONResponse({"detail": {"code": exc.code, "message": str(exc)}}, exc.status_code)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"service": "sewer-agent", "status": "alive", "version": "0.1.0"}

    @app.exception_handler(MediaError)
    async def media_error(request: Request, exc: MediaError):
        return JSONResponse({"detail": {"code": exc.code, "message": str(exc)}}, exc.status_code)

    @app.exception_handler(MissingRecord)
    async def missing(request: Request, exc: MissingRecord):
        return JSONResponse(
            {"detail": {"code": "not_found", "message": "会话、任务或资产不存在"}}, 404
        )

    @app.exception_handler(SubmissionConflict)
    async def conflict(request: Request, exc: SubmissionConflict):
        return JSONResponse(
            {"detail": {"code": "submission_conflict", "message": "提交标识已用于其他内容"}}, 409
        )

    @app.exception_handler(psycopg.Error)
    async def database_error(request: Request, exc: psycopg.Error):
        return JSONResponse(
            {"detail": {"code": "storage_unavailable", "message": "存储暂不可用，请稍后重试"}}, 503
        )

    @app.exception_handler(OSError)
    async def file_error(request: Request, exc: OSError):
        return JSONResponse(
            {"detail": {"code": "storage_unavailable", "message": "文件存储暂不可用"}}, 503
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        if exc.detail == UPLOAD_LIMIT_DETAIL:
            return JSONResponse(
                {"detail": {"code": "upload_too_large", "message": "请求超过大小限制"}}, 413
            )
        if exc.status_code == 400 and isinstance(exc.detail, str):
            return JSONResponse(
                {"detail": {"code": "invalid_request", "message": "请求或上传表单格式无效"}}, 400
            )
        return await http_exception_handler(request, exc)

    return app
