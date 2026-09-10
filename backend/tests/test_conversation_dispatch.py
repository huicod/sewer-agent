from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pipe_inspector.agent.conversation import ConversationDispatcher
from pipe_inspector.contracts.assets import AssetRef
from pipe_inspector.contracts.conversation import ConversationIntent
from pipe_inspector.contracts.intake import TaskRecord
from pipe_inspector.contracts.tasks import TaskSnapshot
from pipe_inspector.contracts.turns import TurnRecord
from pipe_inspector.workflow.start import InspectionStartError


class Store:
    def __init__(self):
        now = datetime.now(timezone.utc)
        self.asset = AssetRef(asset_id=uuid4())
        self.turn = TurnRecord(
            request_id=uuid4(), session_id=uuid4(), text="检查", created_at=now, updated_at=now
        )
        self.task = TaskRecord(
            request_id=uuid4(),
            session_id=self.turn.session_id,
            input_asset=self.asset,
            instruction="检查",
            created_at=now,
            snapshot=TaskSnapshot(
                task_id=uuid4(),
                updated_at=now,
                runtime_status="completed",
                inspection_result="normal",
                coverage={
                    "sufficient_quality": True,
                    "sufficient_coverage": True,
                    "negative_audit_complete": True,
                },
            ),
        )
        self.created = []
        self.steps = {}

    def get_task(self, task_id):
        return self.task

    def get_asset(self, ref):
        return SimpleNamespace(kind="image")

    def list_turns(self, session_id, limit=50):
        return ()

    def load_step(self, task_id, step):
        return None

    def save_turn_step(self, request_id, step, payload):
        return self.steps.setdefault(step, payload)

    def create_task(self, submission):
        self.created.append(submission)
        return self.task

    def update_turn(self, request_id, **changes):
        self.turn = TurnRecord.model_validate(self.turn.model_dump() | changes)
        return self.turn


async def test_evidence_question_reads_saved_normal_without_starting_detector():
    store = Store()
    turn = TurnRecord.model_validate(
        store.turn.model_dump() | {"task_id": store.task.snapshot.task_id}
    )
    starter = SimpleNamespace(start=AsyncMock())
    result = await ConversationDispatcher(store, starter).handle(
        turn, ConversationIntent(kind="ask", message="提议不是事实")
    )
    assert "正常" in result["reply"]
    assert "提议不是事实" not in result["reply"]
    assert result["linked_task_id"] == str(store.task.snapshot.task_id)
    assert not store.created and starter.start.call_count == 0


async def test_inspection_uses_turn_identity_and_can_force_requested_vlm():
    store = Store()
    turn = TurnRecord.model_validate(
        store.turn.model_dump() | {"input_asset": store.asset, "text": "检查图片并执行 VLM 复检"}
    )
    starter = SimpleNamespace(start=AsyncMock())
    result = await ConversationDispatcher(store, starter).handle(
        turn, ConversationIntent(kind="inspect", actions=("verify",), message="检查图片")
    )
    assert store.created[0].request_id == turn.request_id
    assert store.created[0].input_asset == store.asset
    assert starter.start.call_args.kwargs["force_review"] is True
    assert len(starter.start.call_args.kwargs["plan"].target_labels) == 5
    assert "已启动" in result["reply"] and "检查结果" in result["reply"]


async def test_unavailable_tool_is_rejected_before_any_task_is_created():
    store = Store()
    turn = TurnRecord.model_validate(store.turn.model_dump() | {"input_asset": store.asset})
    starter = SimpleNamespace(start=AsyncMock())
    result = await ConversationDispatcher(store, starter).handle(
        turn, ConversationIntent(kind="inspect", actions=("segment",), message="分割")
    )
    assert "未部署" in result["reply"]
    assert not store.created and starter.start.call_count == 0


async def test_inspect_without_media_and_supplement_do_not_fake_execution():
    store = Store()
    starter = SimpleNamespace(start=AsyncMock())
    dispatcher = ConversationDispatcher(store, starter)
    missing = await dispatcher.handle(
        store.turn, ConversationIntent(kind="inspect", message="检查")
    )
    unsupported = await dispatcher.handle(
        store.turn, ConversationIntent(kind="supplement", actions=("verify",), message="再次复检")
    )
    assert "上传" in missing["reply"]
    assert "选择" in unsupported["reply"]
    assert not store.created and starter.start.call_count == 0


async def test_start_failure_preserves_created_task_link_for_recovery():
    store = Store()
    turn = TurnRecord.model_validate(store.turn.model_dump() | {"input_asset": store.asset})
    starter = SimpleNamespace(
        start=AsyncMock(side_effect=InspectionStartError("workflow_unavailable", "未确认"))
    )
    with pytest.raises(InspectionStartError):
        await ConversationDispatcher(store, starter).handle(
            turn, ConversationIntent(kind="inspect", message="检查")
        )
    assert store.turn.linked_task_id == store.task.snapshot.task_id


@pytest.mark.parametrize("start_fails", [False, True])
async def test_supplement_persists_child_link_before_start(start_fails):
    store = Store()
    parent_id = store.task.snapshot.task_id
    turn = TurnRecord.model_validate(store.turn.model_dump() | {"task_id": parent_id})
    child = SimpleNamespace(snapshot=SimpleNamespace(task_id=uuid4()))

    async def start(child_id):
        assert child_id == child.snapshot.task_id == store.turn.linked_task_id
        if start_fails:
            raise InspectionStartError("workflow_unavailable", "未确认")

    starter = SimpleNamespace(
        admit_supplement=AsyncMock(return_value=child), start=AsyncMock(side_effect=start)
    )
    dispatch = ConversationDispatcher(store, starter)
    intent = ConversationIntent(
        kind="supplement", actions=("verify", "export_report"), message="再复检一次"
    )
    if start_fails:
        with pytest.raises(InspectionStartError):
            await dispatch.handle(turn, intent)
    else:
        result = await dispatch.handle(turn, intent)
        assert result["linked_task_id"] == str(child.snapshot.task_id)
        assert "原结果" in result["reply"]
    args = starter.admit_supplement.call_args.args
    assert args[0] == parent_id
    assert args[1].request_id == turn.request_id
    assert args[1].input_asset == store.asset
    assert not store.created


@pytest.mark.parametrize("invalid", ["tool", "asset"])
async def test_supplement_rejects_unavailable_tools_and_asset_substitution(invalid):
    store = Store()
    turn = TurnRecord.model_validate(
        store.turn.model_dump()
        | {
            "task_id": store.task.snapshot.task_id,
            "input_asset": AssetRef(asset_id=uuid4()) if invalid == "asset" else None,
        }
    )
    starter = SimpleNamespace(admit_supplement=AsyncMock(), start=AsyncMock())
    result = await ConversationDispatcher(store, starter).handle(
        turn,
        ConversationIntent(
            kind="supplement",
            actions=("verify", "segment") if invalid == "tool" else ("verify",),
            message="追加",
        ),
    )
    assert "未执行" in result["reply"]
    starter.admit_supplement.assert_not_awaited()
    starter.start.assert_not_awaited()
