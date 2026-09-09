from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from pipe_inspector.api.app import create_app
from pipe_inspector.config import Settings
from pipe_inspector.workflow.start import InspectionStartError


@pytest.mark.asyncio
async def test_start_failure_is_safe_and_structured():
    starter = AsyncMock()
    starter.start.side_effect = InspectionStartError("workflow_unavailable", "请重试")
    app = create_app(Settings(_env_file=None), starter=starter)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(f"/api/tasks/{uuid4()}/start")
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "workflow_unavailable", "message": "请重试"}}
    starter.start.assert_awaited_once()


@pytest.mark.asyncio
async def test_start_validates_task_id_without_contacting_temporal():
    starter = AsyncMock()
    app = create_app(Settings(_env_file=None), starter=starter)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/api/tasks/not-a-uuid/start")
    assert response.status_code == 422
    starter.start.assert_not_awaited()
