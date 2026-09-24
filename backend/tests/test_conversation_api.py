from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
from pipe_inspector.api.app import create_app
from pipe_inspector.config import Settings
from pipe_inspector.contracts.turns import TurnRecord
from pipe_inspector.storage.conversation import ConversationBusy
from pipe_inspector.workflow.start import InspectionStartError


async def test_conversation_post_get_and_cross_session_read():
    now = datetime.now(timezone.utc)
    record = TurnRecord(
        request_id=uuid4(), session_id=uuid4(), text="报告", created_at=now, updated_at=now
    )
    limits = []

    def list_turns(_, limit=50):
        limits.append(limit)
        return (record,)

    repo = SimpleNamespace(get_turn=lambda _: record, list_turns=list_turns)
    starter = SimpleNamespace(submit=AsyncMock(return_value=record))
    app = create_app(Settings(_env_file=None), repository=repo, conversation_starter=starter)
    path = f"/api/sessions/{record.session_id}/turns"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            path, json={"request_id": str(record.request_id), "text": record.text}
        )
        assert response.status_code == 202
        assert response.json() == record.model_dump(mode="json")
        assert (await client.get(path)).json() == [record.model_dump(mode="json")]
        assert limits == [None]
        assert (await client.get(f"{path}/{record.request_id}")).status_code == 200
        assert (
            await client.get(f"/api/sessions/{uuid4()}/turns/{record.request_id}")
        ).status_code == 404


async def test_conversation_busy_and_ambiguous_start_errors_are_safe():
    starter = SimpleNamespace(
        submit=AsyncMock(
            side_effect=[ConversationBusy(), InspectionStartError("workflow_unavailable", "请重试")]
        )
    )
    app = create_app(Settings(_env_file=None), conversation_starter=starter)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        path = f"/api/sessions/{uuid4()}/turns"
        body = {"request_id": str(uuid4()), "text": "报告"}
        first = await client.post(path, json=body)
        assert first.status_code == 409
        assert first.json()["detail"]["code"] == "turn_in_progress"
        second = await client.post(path, json=body)
        assert second.status_code == 503
        assert second.json()["detail"]["code"] == "workflow_unavailable"
