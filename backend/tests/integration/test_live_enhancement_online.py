"""Explicit paid smoke: uploaded CCTV -> conversation -> durable image enhancement.

At most three provider calls and one enhancement inference; no automatic live opt-in.
Artifacts persist in the explicitly chosen output directory, the test DB schema does not.
"""

import asyncio
import json
import logging
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from html import escape
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from pipe_inspector.agent.api_client import APIClientError, OpenAICompatibleClient
from pipe_inspector.agent.enhancement_review import EnhancementReviewer
from pipe_inspector.agent.image_review import ImageReviewer
from pipe_inspector.agent.intent import IntentParser
from pipe_inspector.api.app import create_app
from pipe_inspector.config import Settings
from pipe_inspector.contracts.assets import AssetRecord
from pipe_inspector.media import MediaStore
from pipe_inspector.storage.human_review import HumanReviewRepository
from pipe_inspector.workflow.activities import ImageActivities
from pipe_inspector.workflow.conversation import ConversationWorkflow
from pipe_inspector.workflow.conversation_activities import TurnActivities
from pipe_inspector.workflow.enhancement_activities import EnhancementActivities
from pipe_inspector.workflow.human_review_activities import HumanReviewActivities
from pipe_inspector.workflow.image import ImageInspectionWorkflow
from psycopg import sql
from temporalio.client import Client
from temporalio.worker import Worker

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live_api,
    pytest.mark.skipif(
        os.getenv("SEWER_RUN_LIVE_ENHANCEMENT") != "1",
        reason="explicit paid API and local GPU opt-in required",
    ),
]


async def test_uploaded_cctv_conversation_enhancement():
    image_path = Path(os.environ["SEWER_LIVE_IMAGE"]).resolve()
    output = Path(os.environ["SEWER_ENHANCEMENT_OUTPUT"]).resolve()
    output.mkdir(parents=True, exist_ok=False)
    settings = Settings(asset_root=output / "assets", task_queue="test-enhance-live-" + uuid4().hex)
    assert settings.llm_configured and settings.vlm_configured
    assert settings.image_review_mode == "candidate_review"
    assert settings.temporal_address == "127.0.0.1:7233"
    schema = "test_enhance_live_" + uuid4().hex
    provider_calls, gpu_calls, handles, stages = [], [], [], []
    started = time.monotonic()
    logging.getLogger("temporalio.activity").setLevel(logging.CRITICAL)

    class BoundedClient(OpenAICompatibleClient):
        async def generate(self, messages, response_model):
            if len(provider_calls) >= 3:
                raise APIClientError("smoke_api_budget_exhausted", retryable=False)
            record = {"response_model": response_model.__name__, "status": "started"}
            provider_calls.append(record)
            print("live API", len(provider_calls), record["response_model"], flush=True)
            try:
                result = await super().generate(messages, response_model)
            except APIClientError as exc:
                record.update(status="failed", error_code=exc.code)
                raise
            record["status"] = "succeeded"
            return result

    def api(role):
        return BoundedClient(
            getattr(settings, f"{role}_base_url"),
            getattr(settings, f"{role}_model"),
            getattr(settings, f"{role}_api_key"),
            timeout_seconds=settings.api_timeout_seconds,
            enable_thinking=settings.api_enable_thinking,
            max_output_tokens=settings.api_max_output_tokens,
        )

    def model_factory(kind, checkpoint, *, device):
        if gpu_calls:
            raise ValueError("smoke enhancement budget exhausted")
        from pipe_inspector.vision.enhancement_models import LocalEnhancementModel

        gpu_calls.append(str(kind))
        print("live GPU enhancement", kind, flush=True)
        return LocalEnhancementModel(kind, checkpoint, device=device)

    def save(name, value):
        (output / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    with psycopg.connect(settings.database_dsn.get_secret_value()) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    task_id = None
    repo = HumanReviewRepository(settings.database_dsn, schema=schema)
    repo.initialize()
    media = MediaStore(settings.asset_root)
    originals = ImageActivities(settings, repo, media, reviewer=ImageReviewer(api("vlm")))
    enhanced = EnhancementActivities(
        settings, repo, media, model_factory=model_factory, reviewer=EnhancementReviewer(api("vlm"))
    )
    turns = TurnActivities(settings, repo, parser=IntentParser(api("llm")))
    human = HumanReviewActivities(repo)
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
                        originals.publish_stage,
                        originals.verify_image,
                        originals.finish_image,
                        enhanced.prepare_image_enhancement,
                        enhanced.review_image_enhancement,
                        human.apply_human_review,
                        human.cancel_human_review,
                    ],
                    activity_executor=executor,
                ),
                Worker(
                    client,
                    task_queue=settings.task_queue + "-gpu",
                    activities=[originals.detect_image, enhanced.enhance_image],
                    activity_executor=executor,
                    max_concurrent_activities=1,
                ),
            ):
                app = create_app(settings, repository=repo)
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as http:
                    session = (await http.post("/api/sessions")).json()
                    with image_path.open("rb") as source:
                        uploaded = await http.post(
                            "/api/assets",
                            files={
                                "file": (image_path.name, source, "image/jpeg"),
                            },
                        )
                    assert uploaded.status_code == 201
                    asset = AssetRecord.model_validate(uploaded.json())
                    request = {
                        "request_id": str(uuid4()),
                        "input_asset": asset.ref.model_dump(mode="json"),
                        "text": "新建图片检查，先完成五类检测和VLM复检，再对原图像素区域"
                        "[150,50,406,306]执行局部超分，并辅助复核。不要执行其他增强。",
                    }
                    save("request.json", request)
                    response = await http.post(
                        f"/api/sessions/{session['session_id']}/turns", json=request
                    )
                    assert response.status_code == 202
                    turn_handle = client.get_workflow_handle(
                        "conversation:" + request["request_id"]
                    )
                    handles.append(turn_handle)
                    async with asyncio.timeout(220):
                        turn = await turn_handle.result()
                    save("turn.json", turn)
                    assert turn["status"] == "completed" and turn["linked_task_id"]
                    task_id = UUID(turn["linked_task_id"])
                    handles.append(client.get_workflow_handle(f"inspection:{task_id}"))
                    async with asyncio.timeout(330):
                        while True:
                            task = await asyncio.to_thread(repo.get_task, task_id)
                            marker = (task.snapshot.stage.value, task.snapshot.active_tool)
                            if not stages or stages[-1]["stage"] != list(marker):
                                stages.append(
                                    {
                                        "stage": list(marker),
                                        "elapsed_seconds": round(time.monotonic() - started, 2),
                                    }
                                )
                                print("stage", marker, flush=True)
                            if (
                                task.snapshot.is_terminal
                                or task.snapshot.runtime_status == "waiting_review"
                            ):
                                break
                            await asyncio.sleep(0.25)
                    detail = await http.get(f"/api/tasks/{task_id}/enhancement")
                    assert detail.status_code == 200
                    save("enhancement.json", detail.json())
                    data = detail.json()
                    assert data["result"] is not None and data["review"] is not None
                    assert len(provider_calls) == 3 and len(gpu_calls) == 1
                    shutil.copyfile(media.path_for(asset.ref), output / "original.jpg")
                    derived = AssetRecord.model_validate(data["result"]["output_asset"])
                    shutil.copyfile(media.path_for(derived.ref), output / "enhanced.png")
                    (output / "index.html").write_text(
                        '<!doctype html><meta charset="utf-8"><title>在线增强联调</title>'
                        "<h1>原图 / ROI 局部超分</h1>"
                        "<p>流程验证，不是准确率实验；未代替人工审核。</p>"
                        '<div style="display:flex;gap:16px">'
                        '<img src="original.jpg" style="width:48%">'
                        '<img src="enhanced.png" style="width:48%;object-fit:contain"></div>'
                        '<p>原图 ROI：[150,50,406,306]</p><pre style="white-space:pre-wrap">'
                        + escape(json.dumps(data["review"], ensure_ascii=False, indent=2))
                        + "</pre>",
                        encoding="utf-8",
                    )
    finally:
        if task_id is not None:
            save("task.json", repo.get_task(task_id).model_dump(mode="json"))
            save("calls.json", [call.model_dump(mode="json") for call in repo.list_calls(task_id)])
            for step in ("detect", "verify", "enhancement_source", "enhance", "enhancement_review"):
                save(step + ".json", repo.load_step(task_id, step))
        save(
            "execution.json",
            {
                "provider_calls": provider_calls,
                "gpu_enhancement_calls": gpu_calls,
                "stages": stages,
                "elapsed_seconds": time.monotonic() - started,
                "source": str(image_path),
                "llm_model": settings.llm_model,
                "vlm_model": settings.vlm_model,
            },
        )
        for handle in handles:
            if (await handle.describe()).status.name == "RUNNING":
                await handle.terminate("isolated smoke finished; no human decision fabricated")
        with psycopg.connect(settings.database_dsn.get_secret_value()) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
