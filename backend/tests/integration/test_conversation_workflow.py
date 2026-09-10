"""Real local PG/Temporal conversation loop; fake model by default, opt-in live text only."""

import asyncio
import io
import json
import os
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import numpy as np
import psycopg
import pytest
from PIL import Image
from pipe_inspector.agent.api_client import OpenAICompatibleClient
from pipe_inspector.agent.intent import IntentParser
from pipe_inspector.api.app import create_app
from pipe_inspector.config import Settings
from pipe_inspector.contracts.conversation import ConversationIntent
from pipe_inspector.contracts.detection import DetectionBatch
from pipe_inspector.contracts.inspection import ImageReview
from pipe_inspector.contracts.intake import TaskSubmission
from pipe_inspector.contracts.tasks import TaskSnapshot
from pipe_inspector.contracts.turns import TurnInput
from pipe_inspector.media import MediaStore
from pipe_inspector.storage.conversation import ConversationRepository
from pipe_inspector.workflow.activities import ImageActivities
from pipe_inspector.workflow.conversation import ConversationWorkflow
from pipe_inspector.workflow.conversation_activities import TurnActivities
from pipe_inspector.workflow.conversation_start import ConversationStarter
from pipe_inspector.workflow.image import ImageInspectionWorkflow
from pipe_inspector.workflow.start import run_config
from psycopg import sql
from pydantic import SecretStr
from temporalio import activity
from temporalio.client import Client
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("SEWER_RUN_CONVERSATION_TEST") != "1",
        reason="opt-in local PG/Temporal conversation test; no shared-service restart",
    ),
]


@pytest.fixture
def case(tmp_path):
    settings = Settings(task_queue="test-turn-" + uuid4().hex, api_max_output_tokens=512)
    assert settings.temporal_address == "127.0.0.1:7233"
    assert settings.temporal_namespace == "default"
    schema = "test_turn_" + uuid4().hex
    with psycopg.connect(settings.database_dsn.get_secret_value()) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        repo = ConversationRepository(settings.database_dsn, schema=schema)
        repo.initialize()
        settings.asset_root = tmp_path
        media = MediaStore(
            tmp_path, max_upload_bytes=1024**2, max_pixels=100_000, probe_timeout_seconds=5
        )
        buffer = io.BytesIO()
        Image.fromarray(
            np.random.default_rng(1).integers(40, 210, (64, 64, 3), dtype=np.uint8)
        ).save(buffer, "PNG")
        buffer.seek(0)
        asset = media.ingest(buffer)
        repo.save_asset(asset)
        yield settings, repo, media, asset, repo.create_session().session_id
    finally:
        with psycopg.connect(settings.database_dsn.get_secret_value()) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


async def test_http_inspect_then_saved_evidence_and_reports_survive_reopen(case, monkeypatch):
    settings, repo, media, asset, session_id = case
    requests = []

    def provider(request):
        messages = json.loads(request.content)["messages"]
        requests.append(messages)
        kind = "inspect" if len(requests) == 1 else "ask"
        intent = {
            "kind": kind,
            "message": "模型提议不能冒充结果",
            "actions": ["verify"] if kind == "inspect" else [],
        }
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(intent)}}]
            },
        )

    parser = IntentParser(
        OpenAICompatibleClient(
            "https://fixture.invalid/v1",
            "fixture",
            SecretStr("fixture"),
            transport=httpx.MockTransport(provider),
        )
    )
    turns = TurnActivities(settings, repo, parser=parser)
    config = run_config(settings)
    detector = SimpleNamespace(
        **{key: config[key] for key in ("detector_id", "model_version", "label_space_version")}
    )
    detector.predict_image = lambda _: DetectionBatch(
        detector_id=detector.detector_id,
        model_version=detector.model_version,
        label_space_version=detector.label_space_version,
        width=64,
        height=64,
        detections=(),
    )
    reviewer = SimpleNamespace(
        review=AsyncMock(
            return_value=ImageReview(
                image_assessable=True,
                full_image_audited=True,
                candidates=(),
                additional_anomalies=(),
                summary="测试全图阴性审计",
            )
        )
    )
    image_activities = ImageActivities(settings, repo, media, detector=detector, reviewer=reviewer)
    saved = repo.save_turn_step
    lost = False

    def lose_dispatch_ack(request_id, step, payload):
        nonlocal lost
        result = saved(request_id, step, payload)
        if step == "dispatch" and not lost:
            lost = True
            raise psycopg.OperationalError("fixture lost checkpoint acknowledgement")
        return result

    monkeypatch.setattr(repo, "save_turn_step", lose_dispatch_ack)
    client = await Client.connect(settings.temporal_address)
    handles = []
    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            async with (
                Worker(
                    client,
                    task_queue=settings.task_queue,
                    workflows=[ConversationWorkflow, ImageInspectionWorkflow],
                    activities=[
                        turns.parse_turn,
                        turns.dispatch_turn,
                        turns.finish_turn,
                        image_activities.publish_stage,
                        image_activities.verify_image,
                        image_activities.finish_image,
                    ],
                    activity_executor=executor,
                ),
                Worker(
                    client,
                    task_queue=settings.task_queue + "-gpu",
                    activities=[image_activities.detect_image],
                    activity_executor=executor,
                ),
            ):
                app = create_app(settings, repository=repo)
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as http:
                    path = f"/api/sessions/{session_id}/turns"
                    body = {
                        "request_id": str(uuid4()),
                        "text": "检查图片并执行 VLM 复检",
                        "input_asset": asset.ref.model_dump(mode="json"),
                    }
                    first = await http.post(path, json=body)
                    assert first.status_code == 202
                    handle = client.get_workflow_handle("conversation:" + body["request_id"])
                    handles.append(handle)
                    assert (await http.post(path, json=body)).status_code == 202
                    async with asyncio.timeout(30):
                        result = await handle.result()
                    assert result["status"] == "completed" and result["linked_task_id"]
                    image_handle = client.get_workflow_handle(
                        "inspection:" + result["linked_task_id"]
                    )
                    handles.append(image_handle)
                    async with asyncio.timeout(30):
                        image_result = await image_handle.result()
                    assert image_result["inspection_result"] == "normal"
                    assert (
                        repo.get_run(UUID(result["linked_task_id"]))["config"]["force_review"]
                        is True
                    )
                    assert len(repo.get_session(session_id).tasks) == 1
                    assert len(requests) == 1 and reviewer.review.call_count == 1
                    question = {"request_id": str(uuid4()), "text": "为什么判断正常？"}
                    assert (await http.post(path, json=question)).status_code == 202
                    handle = client.get_workflow_handle("conversation:" + question["request_id"])
                    handles.append(handle)
                    async with asyncio.timeout(30):
                        answer = await handle.result()
                    assert (
                        "正常" in answer["reply"] and "模型提议不能冒充结果" not in answer["reply"]
                    )
                    assert answer["linked_task_id"] == result["linked_task_id"]
                    assert len(requests[-1]) == 4  # system + prior user/assistant + current user
                    assert len(repo.get_session(session_id).tasks) == 1
                    assert reviewer.review.call_count == 1
                    report = await http.get(
                        f"/api/tasks/{result['linked_task_id']}/report?format=json"
                    )
                    assert report.status_code == 200
                reopened = ConversationRepository(settings.database_dsn, schema=repo._schema)
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=create_app(settings, repository=reopened)),
                    base_url="http://test",
                ) as http:
                    transcript = (await http.get(path)).json()
                    assert len(transcript) == 2 and transcript[-1] == answer
    finally:
        for handle in handles:
            if (await handle.describe()).status.name == "RUNNING":
                await handle.terminate("test cleanup")


async def test_worker_restart_uses_intent_checkpoint_and_retries_final_storage_only(
    case, monkeypatch
):
    settings, repo, _, _, session_id = case
    parser = SimpleNamespace(
        parse=AsyncMock(return_value=ConversationIntent(kind="ask", message="读取已有证据"))
    )
    dispatcher = SimpleNamespace(
        handle=AsyncMock(return_value={"reply": "尚无证据", "linked_task_id": None})
    )
    turns = TurnActivities(settings, repo, parser=parser, dispatcher=dispatcher)
    parsed = asyncio.Event()

    @activity.defn(name="parse_conversation_turn")
    async def interrupted_parse(run):
        await turns.parse_turn(run)
        parsed.set()
        raise ApplicationError("fixture acknowledgement lost", type="fixture_retry")

    updated = repo.update_turn
    final_attempts = 0

    def fail_first_final(request_id, **changes):
        nonlocal final_attempts
        if changes.get("status") == "completed":
            final_attempts += 1
            if final_attempts == 1:
                raise psycopg.OperationalError("fixture storage temporarily unavailable")
        return updated(request_id, **changes)

    monkeypatch.setattr(repo, "update_turn", fail_first_final)
    client = await Client.connect(settings.temporal_address)
    request_id = uuid4()
    handle = client.get_workflow_handle(f"conversation:{request_id}")
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            async with Worker(
                client,
                task_queue=settings.task_queue,
                workflows=[ConversationWorkflow],
                activities=[interrupted_parse, turns.dispatch_turn, turns.finish_turn],
                activity_executor=executor,
            ):
                await ConversationStarter(settings, repo).submit(
                    session_id, TurnInput(request_id=request_id, text="查已有结果")
                )
                async with asyncio.timeout(15):
                    await parsed.wait()
            # Worker replacement replays persisted Temporal history and the PG intent checkpoint.
            async with Worker(
                client,
                task_queue=settings.task_queue,
                workflows=[ConversationWorkflow],
                activities=[turns.parse_turn, turns.dispatch_turn, turns.finish_turn],
                activity_executor=executor,
            ):
                async with asyncio.timeout(20):
                    result = await handle.result()
            assert result["status"] == "completed"
            assert parser.parse.call_count == 1 and dispatcher.handle.call_count == 1
            assert final_attempts == 2
            assert repo.get_turn(request_id).reply == "尚无证据"
    finally:
        if (await handle.describe()).status.name == "RUNNING":
            await handle.terminate("test cleanup")


@pytest.mark.live_api
@pytest.mark.skipif(os.getenv("SEWER_RUN_LIVE_API") != "1", reason="explicit paid text API opt-in")
async def test_real_qwen_conversation_reads_only_saved_evidence(case):
    settings, repo, _, asset, session_id = case
    assert settings.llm_configured
    task = repo.create_task(
        TaskSubmission(
            request_id=uuid4(),
            session_id=session_id,
            input_asset=asset.ref,
            instruction="合成测试记录；不代表真实检测",
        )
    )
    repo.update_snapshot(
        TaskSnapshot.model_validate(
            task.snapshot.model_dump()
            | {
                "runtime_status": "completed",
                "stage": "finished",
                "inspection_result": "normal",
                "coverage": {
                    "sufficient_coverage": True,
                    "sufficient_quality": True,
                    "negative_audit_complete": True,
                },
            }
        )
    )
    turns = TurnActivities(settings, repo)
    client = await Client.connect(settings.temporal_address)
    request_id = uuid4()
    handle = client.get_workflow_handle(f"conversation:{request_id}")
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            async with Worker(
                client,
                task_queue=settings.task_queue,
                workflows=[ConversationWorkflow],
                activities=[turns.parse_turn, turns.dispatch_turn, turns.finish_turn],
                activity_executor=executor,
            ):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=create_app(settings, repository=repo)),
                    base_url="http://test",
                ) as http:
                    response = await http.post(
                        f"/api/sessions/{session_id}/turns",
                        json={
                            "request_id": str(request_id),
                            "text": "请根据已有检测结果解释是否正常，不要进行新的检测。",
                            "task_id": str(task.snapshot.task_id),
                        },
                    )
                    assert response.status_code == 202
                    async with asyncio.timeout(90):
                        result = await handle.result()
                    assert result["status"] == "completed"
                    assert repo.load_turn_step(request_id, "intent")["kind"] == "ask"
                    assert "正常" in result["reply"]
                    assert repo.get_run(task.snapshot.task_id) is None
                    assert not repo.list_calls(task.snapshot.task_id)
                    print(
                        f"live conversation model={settings.llm_model}; "
                        "saved-evidence answer; no detector/VLM run"
                    )
    finally:
        if (await handle.describe()).status.name == "RUNNING":
            await handle.terminate("test cleanup")
