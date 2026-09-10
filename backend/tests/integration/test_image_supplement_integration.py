"""Real PG/Temporal linked re-review, with deterministic API fixtures and no GPU worker."""

import asyncio
import io
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from PIL import Image
from pipe_inspector.agent.api_client import OpenAICompatibleClient
from pipe_inspector.agent.image_policy import decide_image
from pipe_inspector.agent.image_review import ImageReviewer
from pipe_inspector.agent.intent import IntentParser
from pipe_inspector.api.app import create_app
from pipe_inspector.config import Settings
from pipe_inspector.contracts.inspection import ImageObservation, ImageReview
from pipe_inspector.contracts.intake import TaskSubmission
from pipe_inspector.contracts.tasks import TaskSnapshot
from pipe_inspector.media import MediaStore
from pipe_inspector.storage.supplements import SupplementRepository
from pipe_inspector.workflow.conversation import ConversationWorkflow
from pipe_inspector.workflow.conversation_activities import TurnActivities
from pipe_inspector.workflow.start import InspectionStarter, run_config
from pipe_inspector.workflow.supplement import ImageSupplementWorkflow
from pipe_inspector.workflow.supplement_activities import SupplementActivities
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
        reason="opt-in local PG/Temporal; no shared-service restart",
    ),
]


def negative_review():
    return ImageReview(
        image_assessable=True,
        full_image_audited=True,
        candidates=(),
        additional_anomalies=(),
        summary="fixture full-image audit",
    )


@pytest.fixture
def case(tmp_path):
    settings = Settings(task_queue="test-supplement-" + uuid4().hex, asset_root=tmp_path)
    assert settings.temporal_address == "127.0.0.1:7233"
    assert settings.temporal_namespace == "default"
    schema = "test_image_supplement_" + uuid4().hex
    with psycopg.connect(settings.database_dsn.get_secret_value()) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        repo = SupplementRepository(settings.database_dsn, schema=schema)
        repo.initialize()
        media = MediaStore(
            tmp_path, max_upload_bytes=1024**2, max_pixels=100_000, probe_timeout_seconds=5
        )
        buffer = io.BytesIO()
        Image.new("RGB", (32, 32), "gray").save(buffer, "PNG")
        buffer.seek(0)
        asset = media.ingest(buffer)
        repo.save_asset(asset)
        session_id = repo.create_session().session_id
        yield settings, repo, media, asset, session_id
    finally:
        with psycopg.connect(settings.database_dsn.get_secret_value()) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def seed_parent(case, *, other=False):
    settings, repo, _, asset, session_id = case
    parent = repo.create_task(
        TaskSubmission(
            request_id=uuid4(),
            session_id=session_id,
            input_asset=asset.ref,
            instruction="original image inspection",
        )
    )
    observed = ImageObservation.model_validate(
        {
            "detection": {
                "detector_id": "prior-detector",
                "model_version": "prior-weights",
                "label_space_version": "five",
                "width": 32,
                "height": 32,
                "detections": [],
            },
            "quality": {"mean_luminance": 120, "contrast": 40, "sharpness": 500, "adequate": True},
        }
    )
    repo.ensure_run(parent.snapshot.task_id, run_config(settings, observed.detection))
    repo.save_step(parent.snapshot.task_id, "detect", observed.model_dump(mode="json"))
    review = None
    if other:
        review = ImageReview.model_validate(
            negative_review().model_dump()
            | {
                "additional_anomalies": [
                    {"label": "OTHER", "name": "疑似异物", "reason": "visible anomaly"}
                ]
            }
        )
        repo.save_step(
            parent.snapshot.task_id,
            "verify",
            {"review": review.model_dump(mode="json"), "skipped": False},
        )
    decision = decide_image(parent.snapshot.task_id, asset.ref, observed, review)
    snapshot = TaskSnapshot.model_validate(
        parent.snapshot.model_dump()
        | {
            "runtime_status": "partial",
            "stage": "finished",
            "updated_at": datetime.now(timezone.utc),
            "inspection_result": decision.inspection_result,
            "findings": decision.findings,
            "coverage": decision.coverage,
        }
    )
    return repo.update_snapshot(snapshot)


async def cleanup(handles):
    for handle in handles:
        if (await handle.describe()).status.name == "RUNNING":
            await handle.terminate("test cleanup")


@pytest.mark.parametrize("other", [False, True])
async def test_conversation_re_review_preserves_source_budget_and_report(case, monkeypatch, other):
    settings, repo, media, asset, session_id = case
    parent = seed_parent(case, other=other)
    parent_json = parent.model_dump(mode="json")
    provider_calls = []

    def vlm_provider(request):
        provider_calls.append(json.loads(request.content))
        if len(provider_calls) == 1:
            return httpx.Response(429, json={"error": "fixture rate limit"})
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": negative_review().model_dump_json()},
                    }
                ]
            },
        )

    def intent_provider(request):
        text = json.loads(request.content)["messages"][-1]["content"]
        kind = "ask" if "为什么" in text else "supplement"
        intent = {
            "kind": kind,
            "actions": ["verify"] if kind == "supplement" else [],
            "message": "fixture suggestion",
        }
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(intent)}}]
            },
        )

    def api(handler):
        return OpenAICompatibleClient(
            "https://fixture.invalid/v1",
            "fixture",
            SecretStr("fixture"),
            transport=httpx.MockTransport(handler),
        )

    turns = TurnActivities(settings, repo, parser=IntentParser(api(intent_provider)))
    activities = SupplementActivities(
        settings, repo, media, reviewer=ImageReviewer(api(vlm_provider))
    )
    original_save = repo.save_turn_step
    dispatch_attempts = 0

    def lose_first_dispatch_ack(request_id, step, payload):
        nonlocal dispatch_attempts
        if step == "dispatch":
            dispatch_attempts += 1
            if dispatch_attempts == 1:
                raise psycopg.OperationalError("fixture failure before saving dispatch")
        return original_save(request_id, step, payload)

    monkeypatch.setattr(repo, "save_turn_step", lose_first_dispatch_ack)
    client = await Client.connect(settings.temporal_address)
    handles = []
    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            async with Worker(
                client,
                task_queue=settings.task_queue,
                workflows=[ConversationWorkflow, ImageSupplementWorkflow],
                activities=[
                    turns.parse_turn,
                    turns.dispatch_turn,
                    turns.finish_turn,
                    activities.verify_image,
                    activities.finish_supplement,
                ],
                activity_executor=executor,
            ):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=create_app(settings, repository=repo)),
                    base_url="http://test",
                ) as http:
                    path = f"/api/sessions/{session_id}/turns"
                    body = {
                        "request_id": str(uuid4()),
                        "task_id": str(parent.snapshot.task_id),
                        "text": "请对原任务追加一次 VLM 复检",
                    }

                    async def send(body):
                        response = await http.post(path, json=body)
                        assert response.status_code == 202
                        handle = client.get_workflow_handle("conversation:" + body["request_id"])
                        handles.append(handle)
                        async with asyncio.timeout(30):
                            return await handle.result()

                    result = await send(body)
                    assert result["status"] == "completed", result.get("error_code")
                    child_id = UUID(result["linked_task_id"])
                    child_handle = client.get_workflow_handle(f"inspection:{child_id}")
                    handles.append(child_handle)
                    async with asyncio.timeout(30):
                        final = await child_handle.result()
                    assert final["runtime_status"] == ("partial" if other else "completed")
                    assert final["inspection_result"] == ("inconclusive" if other else "normal")
                    if other:
                        assert final["findings"][0]["canonical_label"] == "OTHER"
                    replay = await send(body)
                    assert replay["linked_task_id"] == str(child_id)
                    assert (await http.post(f"/api/tasks/{child_id}/start")).status_code == 202
                    answer = await send({"request_id": str(uuid4()), "text": "为什么这样判断？"})
                    assert answer["linked_task_id"] == str(child_id)
                    assert ("无法判定" if other else "正常") in answer["reply"]
                    for target in (parent.snapshot.task_id, child_id):
                        limited = await send(
                            {
                                "request_id": str(uuid4()),
                                "task_id": str(target),
                                "text": "再追加一次复检",
                            }
                        )
                        assert "次数已用完" in limited["reply"]
                    report = await http.get(f"/api/tasks/{child_id}/report?format=json")
                    assert report.status_code == 200
                    assert report.json()["task"]["parent_task_id"] == str(parent.snapshot.task_id)
                    assert {call["tool_name"] for call in report.json()["tool_calls"]} == {"verify"}
                    html = await http.get(f"/api/tasks/{child_id}/report?format=html")
                    assert "原任务报告" in html.text
                    assert (
                        len(provider_calls) == 2
                    )  # one rate-limit, one successful full-image audit
                    assert "image_url" in json.dumps(provider_calls[-1])
                    calls = repo.list_calls(child_id)
                    assert {call.attempt for call in calls} == {1, 2}
                    assert (
                        repo.get_task(parent.snapshot.task_id).model_dump(mode="json")
                        == parent_json
                    )
                    assert not repo.list_calls(parent.snapshot.task_id)
                    reopened = SupplementRepository(settings.database_dsn, schema=repo._schema)
                    assert len(reopened.get_session(session_id).tasks) == 2
                    assert reopened.get_task(child_id).parent_task_id == parent.snapshot.task_id
    finally:
        await cleanup(handles)


async def test_worker_restart_after_verify_checkpoint_retries_only_final_storage(case, monkeypatch):
    settings, repo, media, asset, session_id = case
    parent = seed_parent(case)
    starter = InspectionStarter(settings, repo)
    child = await starter.admit_supplement(
        parent.snapshot.task_id,
        TaskSubmission(
            request_id=uuid4(), session_id=session_id, input_asset=asset.ref, instruction="追加复检"
        ),
    )
    reviewer = SimpleNamespace(review=AsyncMock(return_value=negative_review()))
    activities = SupplementActivities(settings, repo, media, reviewer=reviewer)
    checkpointed = asyncio.Event()

    @activity.defn(name="verify_image")
    async def interrupted_verify(run):
        await activities.verify_image(run)
        checkpointed.set()
        raise ApplicationError("fixture acknowledgement lost", type="fixture_retry")

    client = await Client.connect(settings.temporal_address)
    handle = client.get_workflow_handle(f"inspection:{child.snapshot.task_id}")
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            async with Worker(
                client,
                task_queue=settings.task_queue,
                workflows=[ImageSupplementWorkflow],
                activities=[interrupted_verify, activities.finish_supplement],
                activity_executor=executor,
            ):
                await starter.start(child.snapshot.task_id)
                async with asyncio.timeout(15):
                    await checkpointed.wait()
            reopened = SupplementRepository(settings.database_dsn, schema=repo._schema)
            replaced = SupplementActivities(settings, reopened, media, reviewer=reviewer)
            updated = reopened.update_snapshot
            final_attempts = 0

            def fail_first_final(snapshot):
                nonlocal final_attempts
                if snapshot.is_terminal:
                    final_attempts += 1
                    if final_attempts == 1:
                        raise psycopg.OperationalError("fixture temporary write failure")
                return updated(snapshot)

            monkeypatch.setattr(reopened, "update_snapshot", fail_first_final)
            async with Worker(
                client,
                task_queue=settings.task_queue,
                workflows=[ImageSupplementWorkflow],
                activities=[replaced.verify_image, replaced.finish_supplement],
                activity_executor=executor,
            ):
                async with asyncio.timeout(25):
                    result = await handle.result()
            assert result["inspection_result"] == "normal"
            assert final_attempts == 2
            reviewer.review.assert_awaited_once()
            assert (
                reopened.get_task(child.snapshot.task_id).parent_task_id == parent.snapshot.task_id
            )
            assert len(reopened.list_calls(child.snapshot.task_id)) == 1
    finally:
        await cleanup([handle])
