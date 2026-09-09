"""Bound request bytes before multipart spooling completes, including chunked bodies."""

from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.formparsers import MultiPartException
from starlette.responses import JSONResponse

UPLOAD_LIMIT_DETAIL = "sewer_upload_body_limit"


class BodyLimitMiddleware:
    def __init__(self, app, max_upload_bytes: int):
        self.app = app
        self.max_upload_bytes = max_upload_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            return await self.app(scope, receive, send)
        is_upload = scope["path"] == "/api/assets"
        limit = self.max_upload_bytes + 65536 if is_upload else 65536
        headers = Headers(scope=scope)
        try:
            length = int(headers.get("content-length", "0"))
            if length < 0:
                raise ValueError
        except ValueError:
            return await JSONResponse(
                {"detail": {"code": "invalid_request", "message": "请求长度无效"}}, 400
            )(scope, receive, send)
        if length > limit:
            return await JSONResponse(
                {
                    "detail": {
                        "code": "upload_too_large",
                        "message": "请求超过大小限制",
                    }
                },
                413,
            )(scope, receive, send)
        consumed = 0

        async def bounded_receive():
            nonlocal consumed
            message = await receive()
            if message["type"] == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > limit:
                    if is_upload:
                        # Starlette closes already-open multipart temp files on this exception.
                        raise MultiPartException(UPLOAD_LIMIT_DETAIL)
                    raise HTTPException(
                        413,
                        detail={
                            "code": "upload_too_large",
                            "message": "请求超过大小限制",
                        },
                    )
            return message

        await self.app(scope, bounded_receive, send)
