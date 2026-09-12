"""Conversation admission tests for explicit, image-only enhancement."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from pipe_inspector.agent.conversation import ConversationDispatcher
from pipe_inspector.config import Settings
from pipe_inspector.contracts.assets import AssetRef
from pipe_inspector.contracts.conversation import ConversationIntent
from pipe_inspector.contracts.enhancement import EnhancementOptions
from pipe_inspector.contracts.intake import TaskRecord
from pipe_inspector.contracts.tasks import TaskSnapshot
from pipe_inspector.contracts.turns import TurnRecord
from pipe_inspector.vision.enhancement import ENHANCEMENT_MODELS
from test_conversation_dispatch import Store


def _settings_with_model(tmp_path, kind: str = "dehaze") -> Settings:
    _, filename = ENHANCEMENT_MODELS[kind]
    (tmp_path / filename).write_bytes(b"fixture checkpoint")
    return Settings(_env_file=None, enhancement_models_dir=tmp_path)


def _turn_with_input(store: Store, text: str = "去雾后检查") -> TurnRecord:
    return TurnRecord.model_validate(
        store.turn.model_dump() | {"input_asset": store.asset, "text": text}
    )


class _RecoveryStore(Store):
    def __init__(self, existing: TaskRecord, **kwargs):
        super().__init__(**kwargs)
        self.existing = existing

    def find_task_by_request_id(self, request_id):
        return self.existing if request_id == self.existing.request_id else None


def _existing_task(
    turn: TurnRecord, asset: AssetRef, *, parent_task_id=None, terminal=False
) -> TaskRecord:
    now = datetime.now(timezone.utc)
    snapshot_values = (
        {"runtime_status": "completed", "inspection_result": "inconclusive", "stage": "finished"}
        if terminal
        else {}
    )
    return TaskRecord(
        request_id=turn.request_id,
        session_id=turn.session_id,
        input_asset=asset,
        instruction=turn.text,
        created_at=now,
        snapshot=TaskSnapshot(task_id=uuid4(), updated_at=now, **snapshot_values),
        parent_task_id=parent_task_id,
    )


async def test_new_image_enhancement_preflights_then_starts_with_pinned_plan(tmp_path):
    store = Store(width=640, height=480)
    starter = SimpleNamespace(settings=_settings_with_model(tmp_path), start=AsyncMock())
    intent = ConversationIntent(
        kind="inspect",
        actions=("enhance",),
        enhancement={"kind": "dehaze"},
        message="去雾后辅助复核",
    )

    result = await ConversationDispatcher(store, starter).handle(_turn_with_input(store), intent)

    assert len(store.created) == 1
    starter.start.assert_awaited_once()
    plan = starter.start.call_args.kwargs["plan"]
    assert [step.action for step in plan.steps] == [
        "detect",
        "verify",
        "enhance",
        "summarize",
        "export_report",
    ]
    assert plan.enhancement == EnhancementOptions(kind="dehaze")
    assert "图片任务已启动" in result["reply"]
    assert result["linked_task_id"] == str(store.task.snapshot.task_id)


async def test_enhancement_without_kind_asks_for_one_tool_before_task_creation(tmp_path):
    store = Store(width=640, height=480)
    starter = SimpleNamespace(settings=_settings_with_model(tmp_path), start=AsyncMock())
    intent = ConversationIntent(kind="inspect", actions=("enhance",), message="帮我增强")

    result = await ConversationDispatcher(store, starter).handle(_turn_with_input(store), intent)

    assert all(choice in result["reply"] for choice in ("去雾", "去模糊", "局部超分"))
    assert not store.created
    starter.start.assert_not_awaited()


async def test_super_resolution_without_roi_clarifies_before_task_creation(tmp_path):
    store = Store(width=640, height=480)
    starter = SimpleNamespace(
        settings=Settings(_env_file=None, enhancement_models_dir=tmp_path), start=AsyncMock()
    )
    intent = ConversationIntent(
        kind="inspect",
        actions=("enhance",),
        enhancement={"kind": "super_resolution"},
        message="局部超分",
    )

    result = await ConversationDispatcher(store, starter).handle(_turn_with_input(store), intent)

    assert "ROI" in result["reply"] or "像素框" in result["reply"]
    assert not store.created
    starter.start.assert_not_awaited()


async def test_video_enhancement_is_rejected_before_task_creation(tmp_path):
    store = Store(kind="video", width=640, height=480)
    starter = SimpleNamespace(settings=_settings_with_model(tmp_path), start=AsyncMock())
    intent = ConversationIntent(
        kind="inspect",
        actions=("enhance",),
        enhancement={"kind": "dehaze"},
        message="给视频去雾",
    )

    result = await ConversationDispatcher(store, starter).handle(_turn_with_input(store), intent)

    assert "视频" in result["reply"] and "增强" in result["reply"]
    assert not store.created
    starter.start.assert_not_awaited()


async def test_missing_enhancement_weight_is_reported_before_task_creation(tmp_path):
    store = Store(width=640, height=480)
    starter = SimpleNamespace(
        settings=Settings(_env_file=None, enhancement_models_dir=tmp_path), start=AsyncMock()
    )
    intent = ConversationIntent(
        kind="inspect",
        actions=("enhance",),
        enhancement={"kind": "deblur"},
        message="去模糊",
    )

    result = await ConversationDispatcher(store, starter).handle(_turn_with_input(store), intent)

    assert "模型未部署" in result["reply"]
    assert not store.created
    starter.start.assert_not_awaited()


async def test_enhancement_supplement_reuses_parent_and_passes_only_requested_options(tmp_path):
    store = Store(width=640, height=480)
    parent_id = store.task.snapshot.task_id
    original_parent = store.task.model_dump(mode="json")
    turn = TurnRecord.model_validate(store.turn.model_dump() | {"task_id": parent_id})
    child = SimpleNamespace(snapshot=SimpleNamespace(task_id=uuid4()))
    starter = SimpleNamespace(
        settings=_settings_with_model(tmp_path, "super_resolution"),
        admit_supplement=AsyncMock(return_value=child),
        start=AsyncMock(),
    )
    options = EnhancementOptions(kind="super_resolution", roi_xyxy=(10, 20, 110, 100))
    intent = ConversationIntent(
        kind="supplement",
        actions=("enhance", "verify"),
        enhancement=options,
        message="对这个框做局部超分并辅助复核",
    )

    result = await ConversationDispatcher(store, starter).handle(turn, intent)

    assert not store.created
    assert store.task.model_dump(mode="json") == original_parent
    starter.admit_supplement.assert_awaited_once()
    assert starter.admit_supplement.call_args.args[0] == parent_id
    assert starter.admit_supplement.call_args.kwargs == {"enhancement": options}
    starter.start.assert_awaited_once_with(child.snapshot.task_id)
    assert "关联增强" in result["reply"]
    assert result["linked_task_id"] == str(child.snapshot.task_id)


async def test_supplement_enhancement_rejects_asset_substitution_before_admission(tmp_path):
    store = Store(width=640, height=480)
    turn = TurnRecord.model_validate(
        store.turn.model_dump()
        | {"task_id": store.task.snapshot.task_id, "input_asset": AssetRef(asset_id=uuid4())}
    )
    starter = SimpleNamespace(
        settings=_settings_with_model(tmp_path),
        admit_supplement=AsyncMock(),
        start=AsyncMock(),
    )
    intent = ConversationIntent(
        kind="supplement",
        actions=("enhance",),
        enhancement={"kind": "dehaze"},
        message="追加去雾",
    )

    result = await ConversationDispatcher(store, starter).handle(turn, intent)

    assert "原任务图片" in result["reply"] and "未执行" in result["reply"]
    starter.admit_supplement.assert_not_awaited()
    starter.start.assert_not_awaited()


async def test_inspect_retry_recovers_admitted_task_after_model_file_disappears(tmp_path):
    seed = Store()
    turn = _turn_with_input(seed)
    existing = _existing_task(turn, seed.asset, terminal=True)
    store = _RecoveryStore(existing, width=640, height=480)
    store.turn = turn
    starter = SimpleNamespace(
        settings=Settings(_env_file=None, enhancement_models_dir=tmp_path),
        start=AsyncMock(),
    )
    intent = ConversationIntent(
        kind="inspect",
        actions=("enhance",),
        enhancement={"kind": "dehaze"},
        message="去雾后辅助复核",
    )

    result = await ConversationDispatcher(store, starter).handle(turn, intent)

    assert not store.created
    assert store.turn.linked_task_id == existing.snapshot.task_id
    starter.start.assert_awaited_once()
    assert starter.start.call_args.args == (existing.snapshot.task_id,)
    assert starter.start.call_args.kwargs["plan"].enhancement == intent.enhancement
    assert result["linked_task_id"] == str(existing.snapshot.task_id)
    assert "复用已有" in result["reply"] and "任务和结果" in result["reply"]
    assert "尚未完成" not in result["reply"]


async def test_supplement_retry_recovers_admitted_child_after_model_file_disappears(tmp_path):
    seed = Store()
    parent_id = seed.task.snapshot.task_id
    turn = TurnRecord.model_validate(seed.turn.model_dump() | {"task_id": parent_id})
    child = _existing_task(turn, seed.asset, parent_task_id=parent_id, terminal=True)
    store = _RecoveryStore(child, width=640, height=480)
    store.turn = turn
    store.task = seed.task
    starter = SimpleNamespace(
        settings=Settings(_env_file=None, enhancement_models_dir=tmp_path),
        admit_supplement=AsyncMock(return_value=child),
        start=AsyncMock(),
    )
    options = EnhancementOptions(kind="dehaze")
    intent = ConversationIntent(
        kind="supplement",
        actions=("enhance",),
        enhancement=options,
        message="追加去雾",
    )

    result = await ConversationDispatcher(store, starter).handle(turn, intent)

    starter.admit_supplement.assert_awaited_once()
    assert starter.admit_supplement.call_args.kwargs == {"enhancement": options}
    starter.start.assert_awaited_once_with(child.snapshot.task_id)
    assert store.turn.linked_task_id == child.snapshot.task_id
    assert result["linked_task_id"] == str(child.snapshot.task_id)
    assert "复用已有关联任务和结果" in result["reply"]
    assert "尚未完成" not in result["reply"]
