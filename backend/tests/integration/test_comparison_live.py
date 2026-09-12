"""Real PostgreSQL/Temporal saved comparison, optional single paid intent request.

Inspection records below are synthetic contract fixtures, not accuracy evidence.
No detector, image reviewer, enhancement or GPU worker is registered.
"""

import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import psycopg
import pytest
from pipe_inspector.api.app import create_app
from pipe_inspector.contracts.conversation import ConversationIntent
from pipe_inspector.contracts.intake import TaskSubmission
from pipe_inspector.contracts.tasks import TaskSnapshot
from pipe_inspector.storage.conversation import ConversationRepository
from pipe_inspector.workflow.conversation import ConversationWorkflow
from pipe_inspector.workflow.conversation_activities import TurnActivities
from temporalio import activity
from temporalio.client import Client
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker
from test_conversation_workflow import case as comparison_runtime

comparison_runtime = pytest.fixture(name="comparison_runtime")(comparison_runtime.__wrapped__)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("SEWER_RUN_CONVERSATION_TEST") != "1",
                       reason="explicit local PG/Temporal opt-in required"),
]


def saved_inspection(repo, session_id, asset, *, confirmed, rejected):
    task = repo.create_task(TaskSubmission(
        request_id=uuid4(), session_id=session_id, input_asset=asset.ref,
        instruction="合成比较测试记录，不代表真实管道缺陷或模型准确率",
    ))
    findings = [
        {
            "finding_id": str(uuid4()), "task_id": str(task.snapshot.task_id),
            "canonical_label": "AJ", "source": "detector", "review_status": status,
            "confirmed_by": "rules" if status == "confirmed" else None,
            "evidence": [{"asset": asset.ref.model_dump(mode="json")}],
        }
        for status in [*(["confirmed"] * confirmed), *(["rejected"] * rejected)]
    ]
    return repo.update_snapshot(TaskSnapshot.model_validate(task.snapshot.model_dump() | {
        "runtime_status": "completed", "stage": "finished",
        "inspection_result": "defects_found", "findings": findings,
        "coverage": {"sufficient_quality": True, "sufficient_coverage": True,
                     "negative_audit_complete": True},
    }))


async def test_saved_comparison_survives_lost_dispatch_and_worker_replacement(
    comparison_runtime, monkeypatch,
):
    settings, repo, _, asset, session_id = comparison_runtime
    reference = saved_inspection(repo, session_id, asset, confirmed=1, rejected=0)
    current = saved_inspection(repo, session_id, asset, confirmed=2, rejected=1)
    parser = SimpleNamespace(parse=AsyncMock(return_value=ConversationIntent(
        kind="compare", actions=["compare"], message="提议，不是检测结果",
    )))
    live = os.getenv("SEWER_RUN_LIVE_COMPARISON") == "1"
    if live:
        assert settings.llm_configured
    turns = TurnActivities(settings, repo, parser=None if live else parser)
    persisted = asyncio.Event()
    save_step = repo.save_turn_step

    def lose_dispatch_before_ack(request_id, step, payload):
        if step == "dispatch":
            assert repo.load_turn_step(request_id, "comparison") is not None
            raise psycopg.OperationalError("fixture dispatch completion lost")
        return save_step(request_id, step, payload)

    monkeypatch.setattr(repo, "save_turn_step", lose_dispatch_before_ack)

    @activity.defn(name="dispatch_conversation_turn")
    async def interrupted_dispatch(run):
        try:
            return await turns.dispatch_turn(run)
        except psycopg.OperationalError:
            persisted.set()
            raise ApplicationError("fixture lost completion", type="fixture_retry") from None

    client = await Client.connect(settings.temporal_address)
    request_id = uuid4()
    handle = client.get_workflow_handle(f"conversation:{request_id}")
    path = f"/api/sessions/{session_id}/turns"
    body = {
        "request_id": str(request_id),
        "text": "比较当前任务与界面所选参考任务的已保存检查证据，按类别和审核状态"
        "列出数量差异并提供比较报告。不要重跑模型，不推断真实缺陷新增、消失或维修效果。",
        "task_id": str(current.snapshot.task_id),
        "reference_task_id": str(reference.snapshot.task_id),
    }
    started = False
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            async with Worker(client, task_queue=settings.task_queue,
                              workflows=[ConversationWorkflow],
                              activities=[turns.parse_turn, interrupted_dispatch,
                                          turns.finish_turn],
                              activity_executor=executor):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(
                    app=create_app(settings, repository=repo)), base_url="http://test") as http:
                    response = await http.post(path, json=body)
                    assert response.status_code == 202
                    started = True
                    async with asyncio.timeout(90 if live else 20):
                        await persisted.wait()
            before = repo.load_turn_step(request_id, "comparison")
            assert repo.load_turn_step(request_id, "dispatch") is None
            reopened = ConversationRepository(settings.database_dsn, schema=repo._schema)
            resumed_parser = SimpleNamespace(parse=AsyncMock(side_effect=AssertionError(
                "intent checkpoint must prevent repeat API request")))

            def no_live_source_read(*args):
                raise AssertionError("must read frozen source snapshot")

            monkeypatch.setattr(reopened, "get_task", no_live_source_read)
            resumed = TurnActivities(settings, reopened, parser=resumed_parser)
            async with Worker(client, task_queue=settings.task_queue,
                              workflows=[ConversationWorkflow],
                              activities=[resumed.parse_turn, resumed.dispatch_turn,
                                          resumed.finish_turn],
                              activity_executor=executor):
                async with asyncio.timeout(30):
                    result = await handle.result()
            assert result["status"] == "completed" and result["attempts"] >= 2
            assert result["linked_task_id"] == str(current.snapshot.task_id)
            assert reopened.load_turn_step(request_id, "comparison") == before
            assert before["attempt"] == 1  # actual first successful snapshot, not resume count
            assert before["rows"][0] == {
                "label": "AJ", "reference": {"confirmed": 1, "tentative": 0,
                    "needs_review": 0, "rejected": 0},
                "current": {"confirmed": 2, "tentative": 0,
                    "needs_review": 0, "rejected": 1}, "confirmed_delta": 1,
            }
            resumed_parser.parse.assert_not_called()
            if not live:
                assert parser.parse.call_count == 1
            assert repo.get_task(reference.snapshot.task_id) == reference
            assert repo.get_task(current.snapshot.task_id) == current
            assert len(repo.get_session(session_id).tasks) == 2
            for task in (reference, current):
                assert repo.get_run(task.snapshot.task_id) is None
                assert not repo.list_calls(task.snapshot.task_id)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(
                app=create_app(settings, repository=reopened)), base_url="http://test") as http:
                assert (await http.post(path, json=body)).json() == result
                route = path + f"/{request_id}/comparison"
                snapshot = await http.get(route)
                report = await http.get(route + "/report?format=html")
                json_report = await http.get(route + "/report?format=json")
                assert snapshot.status_code == report.status_code == json_report.status_code == 200
                assert snapshot.json() == json_report.json() == before
                assert "1 → 2" in result["reply"] and "未确认" in result["reply"]
                assert "AJ" in report.text and "合成比较测试记录" in report.text
            artifact = os.getenv("SEWER_COMPARISON_OUTPUT")
            if artifact:
                output = Path(artifact)
                output.mkdir(parents=True, exist_ok=False)
                for name, value in {"request": body, "turn": result,
                                    "intent": repo.load_turn_step(request_id, "intent"),
                                    "comparison": before,
                                    "verification": {"synthetic_inspection_fixtures": True,
                                        "real_postgres_temporal": True, "live_llm_intent": live,
                                        "worker_replaced": True, "snapshot_reused": True,
                                        "inspection_model_calls": 0}}.items():
                    (output / f"{name}.json").write_text(json.dumps(
                        value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                (output / "report.html").write_text(report.text, encoding="utf-8")
    finally:
        if started and (await handle.describe()).status.name == "RUNNING":
            await handle.terminate("isolated comparison test cleanup")
