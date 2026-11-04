"""Local-deployment settings API. Credentials never appear in responses or validation errors."""

import json

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from pipe_inspector.runtime_settings import (
    ConnectionTest,
    RuntimeSettingsError,
    RuntimeSettingsStore,
    SettingsUpdate,
)


def _guard(request: Request, *, write: bool = False):
    origin = request.headers.get("origin")
    expected = str(request.base_url).rstrip("/")
    if origin is not None and origin.rstrip("/") != expected:
        raise RuntimeSettingsError("settings_forbidden", "请从工作台本身打开设置", 403)
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise RuntimeSettingsError("settings_forbidden", "请从工作台本身打开设置", 403)
    if write and (
        request.headers.get("x-sewer-settings") != "1"
        or request.headers.get("content-type", "").split(";")[0].strip() != "application/json"
    ):
        raise RuntimeSettingsError("settings_forbidden", "设置请求无效，请刷新页面重试", 403)


async def _body(request, schema):
    # Deliberately avoid FastAPI's automatic model validation: it returns raw inputs in 422 errors.
    try:
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 32768:
                raise ValueError
        return schema.model_validate(json.loads(body))
    except (ValidationError, ValueError, TypeError):
        raise RuntimeSettingsError(
            "settings_invalid", "请检查 API 地址、模型名称和基础设置的格式", 422
        ) from None


def settings_router(settings):
    router = APIRouter(prefix="/api/settings", tags=["settings"])
    store = RuntimeSettingsStore(settings)

    @router.get("")
    async def read_settings(request: Request):
        _guard(request)
        return JSONResponse(store.public(), headers={"Cache-Control": "no-store"})

    @router.put("")
    async def save_settings(request: Request):
        _guard(request, write=True)
        result = store.save(await _body(request, SettingsUpdate))
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @router.post("/test")
    async def test_connection(request: Request):
        _guard(request, write=True)
        body = await _body(request, ConnectionTest)
        current = store.resolve()
        key = store.role_key(current, body.role, body)
        if not body.base_url or not body.model or not key:
            raise RuntimeSettingsError(
                "settings_key_required", "请填写 API 地址、模型名称和 API Key", 422
            )
        try:
            async with httpx.AsyncClient(
                timeout=min(current.api_timeout_seconds, 15), follow_redirects=False
            ) as client:
                response = await client.get(
                    f"{body.base_url}/models",
                    headers={"Authorization": f"Bearer {key.get_secret_value()}"},
                )
            if response.status_code != 200:
                return {
                    "ok": False,
                    "message": f"模型目录连接未通过（HTTP {response.status_code}）；"
                    "请检查地址和密钥。",
                    "model_found": None,
                }
            content = response.json()
            models = content.get("data") if isinstance(content, dict) else None
            found = (
                any(item.get("id") == body.model for item in models if isinstance(item, dict))
                if isinstance(models, list)
                else None
            )
            return {
                "ok": True,
                "message": "已连接模型目录；这项检查不调用模型推理。"
                if found
                else "已连接服务，但目录中未确认该模型；请核对模型名称。",
                "model_found": found,
            }
        except (httpx.HTTPError, ValueError):
            return {
                "ok": False,
                "message": "暂时无法读取模型目录，请检查地址、网络和 API Key。",
                "model_found": None,
            }

    return router
