"""Conversation comparison uses explicit references and saved evidence, never models."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pipe_inspector.agent.conversation import AVAILABLE_ACTIONS, ConversationDispatcher
from pipe_inspector.agent.intent import IntentParser, compile_inspection_plan
from pipe_inspector.contracts.assets import AssetRecord
from pipe_inspector.contracts.conversation import ConversationIntent
from pipe_inspector.contracts.turns import TurnRecord
from test_conversation_dispatch import Store


def comparison_intent(**changes):
    return ConversationIntent.model_validate(
        {"kind": "compare", "actions": ["compare"], "message": "已修好，不可信模型提议"}
        | changes
    )


def comparison_case(**changes):
    store = Store()
    reference_id = uuid4()
    turn = TurnRecord.model_validate(store.turn.model_dump() | {
        "task_id": store.task.snapshot.task_id,
        "reference_task_id": reference_id,
        "text": "比较所选的两份历史检查证据，不重跑模型",
    } | changes)
    store.load_turn_step = Mock(return_value=None)
    store.save_comparison = Mock()
    starter = SimpleNamespace(start=AsyncMock())
    return store, turn, reference_id, starter


def saved_result(store, turn, reference_id):
    from pipe_inspector.comparison import build_comparison
    from pipe_inspector.contracts.intake import TaskRecord

    reference = TaskRecord.model_validate(store.task.model_dump() | {
        "request_id": uuid4(),
        "snapshot": store.task.snapshot.model_dump() | {"task_id": reference_id},
    })
    media = AssetRecord(ref=store.asset, kind="image", media_type="image/png",
                        byte_size=123, width=640, height=480)
    return build_comparison(turn.request_id, reference, store.task, media, media,
                            attempt=3).model_dump(mode="json")


def test_compare_contract_is_not_a_new_inspection_step():
    parsed = comparison_intent(comparison_reference_task_id=uuid4())
    assert compile_inspection_plan(parsed, available_actions=AVAILABLE_ACTIONS) is None
    assert "compare" not in AVAILABLE_ACTIONS
    assert ConversationIntent(kind="ask", message="读取").comparison_reference_task_id is None


@pytest.mark.parametrize("changes", [
    {"actions": []}, {"actions": ["compare", "verify"]},
    {"actions": ["compare", "enhance"], "auto_enhancement": True},
    {"actions": ["compare", "relative_depth"],
     "geometry": {"operation": "relative_depth"}},
    {"kind": "ask", "actions": [], "comparison_reference_task_id": str(uuid4())},
])
def test_invalid_comparison_actions_and_cross_kind_reference_fail(changes):
    with pytest.raises(ValueError):
        comparison_intent(**changes)


async def test_parser_prompt_describes_compare_as_saved_records_with_no_matching_claim():
    client = SimpleNamespace(generate=AsyncMock(return_value=comparison_intent()))
    await IntentParser(client).parse("比较所选检查")
    prompt = client.generate.call_args.args[0][0]["content"]
    assert "comparison_reference_task_id" in prompt
    assert "本条用户文本" in prompt
    assert "不推断" in prompt and "新增" in prompt


@pytest.mark.parametrize("invalid", ["missing_reference", "missing_current", "upload",
                                     "guessed_reference", "conflicting_reference"])
async def test_ambiguous_comparison_never_starts_or_saves(invalid):
    changes = {"reference_task_id": None} if invalid in {
        "missing_reference", "guessed_reference"} else {}
    if invalid == "missing_current":
        changes["task_id"] = None
    store, turn, _, starter = comparison_case(**changes)
    if invalid == "upload":
        turn = TurnRecord.model_validate(turn.model_dump() | {"input_asset": store.asset})
    parsed = comparison_intent(**(
        {"comparison_reference_task_id": uuid4()}
        if invalid in {"guessed_reference", "conflicting_reference"} else {}
    ))
    result = await ConversationDispatcher(store, starter).handle(turn, parsed)
    assert result["reply"] and "已保存比较" not in result["reply"]
    store.save_comparison.assert_not_called()
    starter.start.assert_not_called()
    assert not store.created


@pytest.mark.parametrize("kind,actions", [("inspect", []), ("ask", []),
                                         ("supplement", ["verify"]), ("report", [])])
async def test_reference_selection_cannot_silently_execute_another_intent(kind, actions):
    store, turn, _, starter = comparison_case()
    result = await ConversationDispatcher(store, starter).handle(
        turn, ConversationIntent(kind=kind, actions=actions, message="提议")
    )
    assert "比较" in result["reply"] and "未执行" in result["reply"]
    store.save_comparison.assert_not_called()
    starter.start.assert_not_called()
    assert not store.created


@pytest.mark.parametrize("via_text", [False, True])
async def test_comparison_passes_bound_ids_and_real_attempt_and_reports_saved_facts(via_text):
    store, turn, reference_id, starter = comparison_case()
    parsed = comparison_intent()
    if via_text:
        turn = TurnRecord.model_validate(turn.model_dump() | {
            "reference_task_id": None, "text": f"与参考任务 {str(reference_id).upper()} 比较",
        })
        parsed = comparison_intent(comparison_reference_task_id=reference_id)
    store.save_comparison.return_value = saved_result(store, turn, reference_id)
    result = await ConversationDispatcher(store, starter).handle(turn, parsed, attempt=3)
    store.save_comparison.assert_called_once_with(
        turn.request_id, reference_id, store.task.snapshot.task_id, attempt=3,
    )
    assert str(reference_id) in result["reply"]
    assert "未确认" in result["reply"] and "AJ" in result["reply"]
    assert "已修好" not in result["reply"]
    assert result["linked_task_id"] == str(store.task.snapshot.task_id)
    starter.start.assert_not_called()
    assert not store.created


async def test_cached_comparison_dispatch_does_not_reread_live_task():
    store, turn, reference_id, starter = comparison_case()
    saved = saved_result(store, turn, reference_id)
    store.load_turn_step.return_value = saved
    store.save_comparison.return_value = saved
    store.get_task = Mock(side_effect=AssertionError("must reuse frozen task"))
    result = await ConversationDispatcher(store, starter).handle(
        turn, comparison_intent(), attempt=4,
    )
    assert result["linked_task_id"] == str(store.task.snapshot.task_id)
    store.get_task.assert_not_called()


async def test_comparison_can_use_last_linked_current_task_from_same_session():
    from datetime import timedelta

    store, turn, reference_id, starter = comparison_case(task_id=None)
    prior = TurnRecord.model_validate(turn.model_dump() | {
        "request_id": uuid4(), "reference_task_id": None,
        "created_at": turn.created_at - timedelta(seconds=1),
        "status": "completed", "stage": "finished", "reply": "已有检查",
        "linked_task_id": store.task.snapshot.task_id,
    })
    store.list_turns = Mock(return_value=(prior, turn))
    store.save_comparison.return_value = saved_result(store, turn, reference_id)
    result = await ConversationDispatcher(store, starter).handle(turn, comparison_intent())
    assert result["linked_task_id"] == str(store.task.snapshot.task_id)
    store.save_comparison.assert_called_once_with(
        turn.request_id, reference_id, store.task.snapshot.task_id, attempt=1,
    )
    starter.start.assert_not_called()


@pytest.mark.parametrize("code,expected", [
    ("comparison_same_task", "不同"), ("comparison_source_not_ready", "结束"),
    ("comparison_media_mismatch", "类型"), ("comparison_original_only", "原始"),
])
async def test_source_preflight_errors_are_actionable_clarifications(code, expected):
    from pipe_inspector.comparison import ComparisonNotAllowed

    store, turn, _, starter = comparison_case()
    store.save_comparison.side_effect = ComparisonNotAllowed(code)
    result = await ConversationDispatcher(store, starter).handle(turn, comparison_intent())
    assert expected in result["reply"]
    starter.start.assert_not_called()


async def test_dispatch_activity_passes_current_attempt_not_stale_turn_count(monkeypatch):
    from pipe_inspector.config import Settings
    from pipe_inspector.workflow.conversation_activities import TurnActivities

    store, turn, _, _ = comparison_case()
    store.turn = turn
    store.get_turn = Mock(return_value=turn)
    store.load_turn_step = Mock(side_effect=[None, comparison_intent().model_dump(mode="json")])
    dispatcher = SimpleNamespace(handle=AsyncMock(return_value={
        "reply": "done", "linked_task_id": None,
    }))
    monkeypatch.setattr("pipe_inspector.workflow.conversation_activities.activity.info",
                        lambda: SimpleNamespace(attempt=4))
    await TurnActivities(Settings(_env_file=None), store, dispatcher=dispatcher).dispatch_turn(
        {"request_id": str(turn.request_id)}
    )
    assert dispatcher.handle.call_args.kwargs == {"attempt": 4}
