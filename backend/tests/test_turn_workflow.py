from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pipe_inspector.agent.api_client import APIClientError
from pipe_inspector.config import Settings
from pipe_inspector.contracts.conversation import ConversationIntent
from pipe_inspector.contracts.turns import TurnRecord
from pipe_inspector.workflow.conversation_activities import TurnActivities
from pipe_inspector.workflow.conversation_start import ConversationStarter, turn_config
from pipe_inspector.workflow.start import InspectionStartError
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment


class TurnStore:
    def __init__(self):
        now = datetime.now(timezone.utc)
        self.turn = TurnRecord(
            request_id=uuid4(), session_id=uuid4(), text="请出报告", created_at=now, updated_at=now
        )
        self.steps = {}
        self.config = turn_config(Settings(_env_file=None))

    def get_turn(self, request_id):
        return self.turn

    def get_turn_config(self, request_id):
        return self.config

    def list_turns(self, session_id, limit=50):
        return (self.turn,)

    def load_turn_step(self, request_id, step):
        return self.steps.get(step)

    def save_turn_step(self, request_id, step, payload):
        return self.steps.setdefault(step, payload)

    def update_turn(self, request_id, **changes):
        if self.turn.status not in {"completed", "failed"}:
            self.turn = TurnRecord.model_validate(self.turn.model_dump() | changes)
        return self.turn

    def admit_turn(self, submission, config):
        return self.turn


async def test_parse_checkpoint_avoids_second_model_call():
    store = TurnStore()
    parser = SimpleNamespace(
        parse=AsyncMock(return_value=ConversationIntent(kind="report", message="导出报告"))
    )
    activities = TurnActivities(Settings(_env_file=None), store, parser=parser)
    run = {"request_id": str(store.turn.request_id), "config": store.config}
    env = ActivityEnvironment()
    first = await env.run(activities.parse_turn, run)
    assert first["kind"] == "report"
    assert await env.run(activities.parse_turn, run) == first
    assert parser.parse.call_count == 1
    assert store.turn.stage == "planning"


async def test_parse_failure_keeps_safe_code_and_durable_failed_reply():
    store = TurnStore()
    parser = SimpleNamespace(
        parse=AsyncMock(side_effect=APIClientError("authentication_failed", retryable=False))
    )
    activities = TurnActivities(Settings(_env_file=None), store, parser=parser)
    run = {"request_id": str(store.turn.request_id), "config": store.config}
    env = ActivityEnvironment()
    with pytest.raises(ApplicationError) as error:
        await env.run(activities.parse_turn, run)
    assert error.value.non_retryable
    result = env.run(activities.finish_turn, {"run": run, "error": "authentication_failed"})
    assert result["status"] == "failed"
    assert result["error_code"] == "authentication_failed"
    assert store.turn.status == "failed"


async def test_ambiguous_conversation_start_reuses_persisted_identity():
    store = TurnStore()
    client = SimpleNamespace(start_workflow=AsyncMock(side_effect=[OSError(), object()]))
    starter = ConversationStarter(
        Settings(_env_file=None), store, connect=AsyncMock(return_value=client)
    )
    with pytest.raises(InspectionStartError):
        await starter.submit(store.turn.session_id, store.turn)
    await starter.submit(store.turn.session_id, store.turn)
    first, second = client.start_workflow.call_args_list
    assert first == second
    assert first.kwargs["id"] == f"conversation:{store.turn.request_id}"


def test_turn_pins_contain_no_credentials_and_reject_secret_urls(tmp_path):
    settings = Settings(_env_file=None, llm_api_key="fixture-secret", llm_model="fixture")
    assert "fixture-secret" not in str(turn_config(settings))
    with pytest.raises(InspectionStartError):
        turn_config(
            Settings(
                _env_file=None,
                runtime_settings_dir=tmp_path / "invalid-endpoint",
                llm_base_url="https://example.invalid/v1?api_key=fixture-secret",
            )
        )
