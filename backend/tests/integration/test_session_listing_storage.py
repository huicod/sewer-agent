"""Conversation navigation projects saved sessions without task/turn duplication."""

from uuid import uuid4

from pipe_inspector.contracts.intake import TaskSubmission
from pipe_inspector.contracts.turns import TurnSubmission

from backend.tests.integration.test_conversation_storage import (
    conversation_store,  # noqa: F401
    save_original_asset,
)
from backend.tests.integration.test_conversation_storage import (
    pytestmark as pytestmark,
)


def test_session_summaries_include_empty_sessions_and_count_rows_once(conversation_store):  # noqa: F811
    store, _, _ = conversation_store
    empty = store.create_session()
    active = store.create_session()
    asset = save_original_asset(store)
    for instruction in ("原图检查", "同图再次检查"):
        store.create_task(
            TaskSubmission(
                request_id=uuid4(),
                session_id=active.session_id,
                input_asset=asset.ref,
                instruction=instruction,
            )
        )
    store.admit_turn(
        TurnSubmission(
            request_id=uuid4(),
            session_id=active.session_id,
            text="检查这张图片",
            input_asset=asset.ref,
        ),
        {},
    )
    summaries = store.list_sessions()
    assert len(summaries) == 2
    assert summaries[0].session_id == active.session_id
    assert summaries[0].task_count == 2
    assert summaries[0].turn_count == 1
    assert summaries[0].title == "检查这张图片"
    assert summaries[1].session_id == empty.session_id
    assert summaries[1].title == "新对话"
    assert summaries[1].task_count == summaries[1].turn_count == 0
