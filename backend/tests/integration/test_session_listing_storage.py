"""Conversation navigation projects saved sessions without task/turn duplication."""

from uuid import uuid4

import psycopg
from pipe_inspector.contracts.intake import SessionUpdate, TaskSubmission
from pipe_inspector.contracts.turns import TurnSubmission
from psycopg import sql

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


def test_session_metadata_persists_partial_updates_and_preserves_history(conversation_store):  # noqa: F811
    store, settings, schema = conversation_store
    session = store.create_session()
    asset = save_original_asset(store)
    task = store.create_task(
        TaskSubmission(
            request_id=uuid4(),
            session_id=session.session_id,
            input_asset=asset.ref,
            instruction="原始推断标题",
        )
    )
    turn = store.admit_turn(
        TurnSubmission(
            request_id=uuid4(),
            session_id=session.session_id,
            text="保留的对话",
            input_asset=asset.ref,
        ),
        {},
    )

    renamed = store.update_session(session.session_id, SessionUpdate(title="我的检查", pinned=True))
    archived = store.update_session(session.session_id, SessionUpdate(archived=True))

    assert renamed.custom_title == "我的检查"
    assert renamed.pinned is True
    assert archived.custom_title == "我的检查"
    assert archived.pinned is True
    assert archived.archived is True
    assert store.get_session(session.session_id).tasks[0].snapshot.task_id == task.snapshot.task_id
    assert store.list_turns(session.session_id)[0].request_id == turn.request_id
    reopened = type(store)(settings.database_dsn, schema=schema)
    assert reopened.list_sessions()[0] == archived
    newer_unpinned = reopened.create_session()
    summaries = reopened.list_sessions()
    assert summaries[0].session_id == session.session_id
    assert summaries[1].session_id == newer_unpinned.session_id
    cleared = reopened.update_session(session.session_id, SessionUpdate(title=None))
    assert cleared.custom_title is None
    assert cleared.title == "保留的对话"


def test_initialize_upgrades_existing_session_table(conversation_store):  # noqa: F811
    store, settings, schema = conversation_store
    with psycopg.connect(settings.database_dsn.get_secret_value()) as connection:
        connection.execute(
            sql.SQL(
                """ALTER TABLE {}
                DROP COLUMN custom_title,
                DROP COLUMN pinned,
                DROP COLUMN archived,
                DROP COLUMN metadata_updated_at"""
            ).format(sql.Identifier(schema, "sewer_sessions"))
        )

    store.initialize()
    session = store.create_session()
    summary = store.list_sessions()[0]
    assert summary.session_id == session.session_id
    assert summary.custom_title is None
    assert summary.pinned is False
    assert summary.archived is False
