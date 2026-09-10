from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from pipe_inspector.api.app import create_app
from pipe_inspector.config import Settings
from pipe_inspector.contracts.assets import AssetKind
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


@pytest.mark.asyncio
async def test_cancel_returns_task_and_video_detail_reads_only_saved_steps():
    task_id = uuid4()
    task = {
        "request_id": str(uuid4()),
        "session_id": str(uuid4()),
        "input_asset": {"asset_id": str(uuid4()), "version": 1},
        "instruction": "scan video",
        "created_at": "2026-09-10T00:00:00Z",
        "snapshot": {"task_id": str(task_id), "updated_at": "2026-09-10T00:00:00Z"},
        "parent_task_id": None,
    }
    from pipe_inspector.contracts.intake import TaskRecord

    record = TaskRecord.model_validate(task)
    evidence = {
        "frames": [
            {
                "frame_index": 4,
                "timestamp_seconds": 1.25,
                "asset": {"asset_id": str(uuid4()), "version": 1},
            }
        ],
        "review_frames": [4],
    }
    repo = SimpleNamespace(
        get_task=lambda _: record,
        get_asset=lambda _: SimpleNamespace(kind=AssetKind.VIDEO),
        load_step=lambda _, step: {"decode_complete": True} if step == "video_scan" else evidence,
    )
    starter = SimpleNamespace(cancel=AsyncMock(return_value=record))
    app = create_app(Settings(_env_file=None), repository=repo, starter=starter)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        cancelled = await client.post(f"/api/tasks/{task_id}/cancel")
        detail = await client.get(f"/api/tasks/{task_id}/video")

    assert cancelled.status_code == 202
    assert cancelled.json() == record.model_dump(mode="json")
    starter.cancel.assert_awaited_once_with(task_id)
    assert detail.status_code == 200
    assert detail.json() == {
        "task_id": str(task_id),
        "scan": {"decode_complete": True},
        "evidence": evidence,
    }


@pytest.mark.asyncio
async def test_video_detail_rejects_image_without_loading_video_steps():
    task_id = uuid4()
    repo = SimpleNamespace(
        get_task=lambda _: SimpleNamespace(input_asset=object()),
        get_asset=lambda _: SimpleNamespace(kind=AssetKind.IMAGE),
        load_step=lambda *_: pytest.fail("image detail must not read video checkpoints"),
    )
    app = create_app(Settings(_env_file=None), repository=repo, starter=AsyncMock())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"/api/tasks/{task_id}/video")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "video_only"
