from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pipe_inspector.agent.conversation import ConversationDispatcher
from pipe_inspector.contracts.assets import AssetRef
from pipe_inspector.contracts.conversation import ConversationIntent
from pipe_inspector.contracts.intake import TaskRecord
from pipe_inspector.contracts.turns import TurnRecord
from pipe_inspector.storage.supplements import SupplementNotAllowed
from pipe_inspector.workflow.start import InspectionStartError
from test_conversation_dispatch import Store


def frame_case():
    store = Store(kind="frame")
    frame = AssetRef(asset_id=uuid4())
    turn = TurnRecord.model_validate(store.turn.model_dump() | {
        "task_id": store.task.snapshot.task_id,
        "input_asset": frame,
        "text": "对所选视频证据帧去雾",
    })
    child = SimpleNamespace(snapshot=SimpleNamespace(task_id=uuid4(), is_terminal=False))
    starter = SimpleNamespace(
        admit_frame_tool=AsyncMock(return_value=child), start=AsyncMock()
    )
    return store, turn, child, starter


@pytest.mark.parametrize("kind", ["inspect", "supplement"])
@pytest.mark.parametrize("operation", ["enhance", "relative_depth", "segment"])
async def test_selected_frame_runs_only_the_explicit_tool(kind, operation):
    store, turn, child, starter = frame_case()
    options = (
        {"enhancement": {"kind": "dehaze"}}
        if operation == "enhance"
        else {"geometry": {
            "operation": operation,
            "roi_xyxy": [10, 20, 60, 80] if operation == "segment" else None,
        }}
    )
    intent = ConversationIntent(
        kind=kind, actions=(operation,), message="执行所选帧工具", **options
    )
    result = await ConversationDispatcher(store, starter).handle(turn, intent)

    starter.admit_frame_tool.assert_awaited_once()
    args, kwargs = starter.admit_frame_tool.call_args
    assert args[0] == turn.task_id
    assert args[1].input_asset == turn.input_asset
    assert args[1].request_id == turn.request_id
    assert kwargs == ({"enhancement": intent.enhancement} if operation == "enhance"
                      else {"geometry": intent.geometry})
    assert store.turn.linked_task_id == child.snapshot.task_id
    starter.start.assert_awaited_once_with(child.snapshot.task_id)
    assert "仅针对所选帧" in result["reply"] and "不重新扫描" in result["reply"]
    assert not store.created


@pytest.mark.parametrize("intent", [
    ConversationIntent(kind="ask", message="解释所选帧"),
    ConversationIntent(kind="supplement", actions=("verify",), message="再次复检"),
    ConversationIntent(kind="supplement", actions=("enhance",), auto_enhancement=True,
                       message="允许按需增强"),
    ConversationIntent(kind="supplement", actions=("enhance",), message="增强"),
    ConversationIntent(kind="supplement", actions=("enhance", "verify"),
                       enhancement={"kind": "dehaze"}, message="再跑原图VLM"),
    ConversationIntent(kind="supplement", actions=("segment",),
                       geometry={"operation": "segment", "finding_id": str(uuid4())},
                       message="分割发现"),
])
async def test_frame_selection_does_not_silently_run_other_actions(intent):
    store, turn, _, starter = frame_case()
    result = await ConversationDispatcher(store, starter).handle(turn, intent)
    assert "所选帧" in result["reply"]
    starter.admit_frame_tool.assert_not_awaited()
    starter.start.assert_not_awaited()
    assert not store.created


async def test_frame_child_link_is_saved_before_ambiguous_start():
    store, turn, child, starter = frame_case()

    async def unavailable(task_id):
        assert store.turn.linked_task_id == child.snapshot.task_id == task_id
        raise InspectionStartError("workflow_unavailable", "未确认")

    starter.start.side_effect = unavailable
    intent = ConversationIntent(kind="supplement", actions=("enhance",),
                                enhancement={"kind": "dehaze"}, message="去雾")
    with pytest.raises(InspectionStartError):
        await ConversationDispatcher(store, starter).handle(turn, intent)


@pytest.mark.parametrize("error", [
    SupplementNotAllowed("frame_tool_limit"),
    SupplementNotAllowed("source_evidence_missing"),
    InspectionStartError("enhancement_unavailable", "所选增强模型未部署", 409),
])
async def test_frame_admission_rejection_preserves_parent(error):
    store, turn, _, starter = frame_case()
    parent = store.task.model_dump(mode="json")
    starter.admit_frame_tool.side_effect = error
    intent = ConversationIntent(kind="supplement", actions=("enhance",),
                                enhancement={"kind": "dehaze"}, message="去雾")
    result = await ConversationDispatcher(store, starter).handle(turn, intent)
    assert "未执行" in result["reply"]
    starter.start.assert_not_awaited()
    assert store.task.model_dump(mode="json") == parent


@pytest.mark.parametrize("kind", ["ask", "report"])
async def test_query_of_completed_frame_child_states_single_frame_scope(kind):
    store = Store()
    store.task = TaskRecord.model_validate(store.task.model_dump() | {
        "parent_task_id": uuid4(),
        "frame_source": {
            "video_asset": AssetRef(asset_id=uuid4()),
            "frame_index": 42,
            "timestamp_seconds": 1.68,
        },
    })
    turn = TurnRecord.model_validate(store.turn.model_dump() | {
        "task_id": store.task.snapshot.task_id,
    })
    starter = SimpleNamespace(start=AsyncMock())
    reply = await ConversationDispatcher(store, starter).handle(
        turn, ConversationIntent(kind=kind, message="查询已存单帧结果")
    )
    assert "帧 42" in reply["reply"] and "1.68 秒" in reply["reply"]
    assert "仅针对所选帧" in reply["reply"]
    starter.start.assert_not_awaited()
