"""Explicit paid/GPU smoke, with persisted evidence and a disposable database schema."""

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
from pipe_inspector.agent.image_review import ImageReviewer
from pipe_inspector.agent.intent import IntentParser
from pipe_inspector.api.app import create_app
from pipe_inspector.config import Settings
from pipe_inspector.contracts.assets import AssetRecord
from pipe_inspector.contracts.geometry import GeometryDetail
from pipe_inspector.media import MediaStore
from pipe_inspector.storage.human_review import HumanReviewRepository
from pipe_inspector.workflow.activities import ImageActivities
from pipe_inspector.workflow.conversation import ConversationWorkflow
from pipe_inspector.workflow.conversation_activities import TurnActivities
from pipe_inspector.workflow.geometry_activities import GeometryActivities
from pipe_inspector.workflow.human_review_activities import HumanReviewActivities
from pipe_inspector.workflow.image import ImageInspectionWorkflow
from psycopg import sql
from temporalio.client import Client
from temporalio.worker import Worker

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live_api,
    pytest.mark.skipif(
        os.getenv("SEWER_RUN_LIVE_GEOMETRY") != "1",
        reason="explicit paid API and local GPU opt-in required",
    ),
]


async def test_uploaded_cctv_dialogue_original_review_then_geometry():
    image_path = Path(os.environ["SEWER_LIVE_IMAGE"]).resolve()
    output = Path(os.environ["SEWER_GEOMETRY_OUTPUT"]).resolve()
    output.mkdir(parents=True, exist_ok=False)
    settings = Settings(asset_root=output / "assets", task_queue="test-geo-live-" + uuid4().hex)
    assert settings.llm_configured and settings.vlm_configured
    assert settings.temporal_address == "127.0.0.1:7233"
    schema = "test_geo_live_" + uuid4().hex
    provider_calls, gpu_calls, handles, stages = [], [], [], []
    started = time.monotonic()
    logging.getLogger("temporalio.activity").setLevel(logging.CRITICAL)

    def save(name, value):
        (output / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    class BoundedClient(OpenAICompatibleClient):
        async def generate(self, messages, response_model):
            if len(provider_calls) >= 2:
                raise APIClientError("smoke_api_budget_exhausted", retryable=False)
            record = {"response_model": response_model.__name__, "status": "started"}
            provider_calls.append(record)
            safe = []
            for message in messages:
                content = message["content"]
                if isinstance(content, list):
                    content = [
                        {"type": "image_url", "image_url": {"url": "[local evidence image]"}}
                        if item.get("type") == "image_url"
                        else item
                        for item in content
                    ]
                safe.append({"role": message["role"], "content": content})
            save(f"api-{len(provider_calls)}-request.json", safe)
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

    def factory(kind):
        def load(checkpoint, *, device):
            from pipe_inspector.vision.geometry_models import LocalRelativeDepth, LocalSegmenter

            if kind in gpu_calls:
                raise ValueError("smoke geometry budget exhausted")
            gpu_calls.append(kind)
            print("live GPU", kind, flush=True)
            return (LocalSegmenter if kind == "segment" else LocalRelativeDepth)(
                checkpoint, device=device
            )

        return load

    with psycopg.connect(settings.database_dsn.get_secret_value()) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    repo = HumanReviewRepository(settings.database_dsn, schema=schema)
    repo.initialize()
    media = MediaStore(settings.asset_root)
    originals = ImageActivities(settings, repo, media, reviewer=ImageReviewer(api("vlm")))
    geometry = GeometryActivities(
        settings, repo, media, segmenter_factory=factory("segment"), depth_factory=factory("depth")
    )
    turns = TurnActivities(settings, repo, parser=IntentParser(api("llm")))
    human = HumanReviewActivities(repo)
    task_id = None
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
                        geometry.prepare_image_geometry,
                        human.apply_human_review,
                        human.cancel_human_review,
                    ],
                    activity_executor=executor,
                ),
                Worker(
                    client,
                    task_queue=settings.task_queue + "-gpu",
                    activities=[
                        originals.detect_image,
                        geometry.segment_image,
                        geometry.estimate_relative_depth,
                    ],
                    activity_executor=executor,
                    max_concurrent_activities=1,
                ),
            ):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=create_app(settings, repository=repo)),
                    base_url="http://test",
                ) as http:
                    session = (await http.post("/api/sessions")).json()
                    with image_path.open("rb") as source:
                        uploaded = await http.post(
                            "/api/assets", files={"file": (image_path.name, source, "image/jpeg")}
                        )
                    assert uploaded.status_code == 201
                    asset = AssetRecord.model_validate(uploaded.json())
                    request = {
                        "request_id": str(uuid4()),
                        "input_asset": asset.ref.model_dump(mode="json"),
                        "text": "新建图片检查，先完成五类检测和 VLM 复检，然后对原图像素框"
                        "[280,66,549,350]执行分割和相对量化（relative_measure），"
                        "给出掩膜、像素面积和该掩膜内的相对深度统计。不要增强图片，不测实际毫米尺寸。",
                    }
                    save("request.json", request)
                    response = await http.post(
                        f"/api/sessions/{session['session_id']}/turns", json=request
                    )
                    assert response.status_code == 202
                    handle = client.get_workflow_handle("conversation:" + request["request_id"])
                    handles.append(handle)
                    async with asyncio.timeout(180):
                        turn = await handle.result()
                    save("turn.json", turn)
                    save("intent.json", repo.load_turn_step(UUID(request["request_id"]), "intent"))
                    assert turn["status"] == "completed" and turn["linked_task_id"]
                    task_id = UUID(turn["linked_task_id"])
                    handles.append(client.get_workflow_handle(f"inspection:{task_id}"))
                    async with asyncio.timeout(300):
                        while True:
                            task = await asyncio.to_thread(repo.get_task, task_id)
                            marker = [task.snapshot.stage.value, task.snapshot.active_tool]
                            if not stages or stages[-1]["stage"] != marker:
                                stages.append(
                                    {"stage": marker, "elapsed_seconds": time.monotonic() - started}
                                )
                                print("stage", marker, flush=True)
                            if (
                                task.snapshot.is_terminal
                                or task.snapshot.runtime_status == "waiting_review"
                            ):
                                break
                            await asyncio.sleep(0.2)
                    response = await http.get(f"/api/tasks/{task_id}/geometry")
                    assert response.status_code == 200
                    detail = GeometryDetail.model_validate(response.json())
                    save("geometry.json", detail.model_dump(mode="json"))
                    assert (
                        detail.segmentation and detail.depth and detail.measurements.relative_depth
                    )
                    assert gpu_calls == ["segment", "depth"] and len(provider_calls) == 2
                    baseline = repo.load_step(task_id, "geometry_source")["decision"]
                    assert [f.model_dump(mode="json") for f in task.snapshot.findings] == baseline[
                        "findings"
                    ]
                    assert task.snapshot.coverage.model_dump(mode="json") == baseline["coverage"]
                    assert task.snapshot.inspection_result == baseline["inspection_result"]
                    shutil.copyfile(media.path_for(asset.ref), output / "original.jpg")
                    cards = ['<figure>原图<img src="original.jpg"></figure>']
                    for name, record in (
                        ("mask.png", detail.segmentation.mask_asset),
                        ("overlay.png", detail.segmentation.overlay_asset),
                        ("depth.png", detail.depth.preview_asset),
                        ("depth.npy", detail.depth.raw_asset),
                    ):
                        fetched = await http.get(
                            f"/api/assets/{record.ref.asset_id}/versions/{record.ref.version}/content"
                        )
                        assert fetched.status_code == 200
                        shutil.copyfile(media.path_for(record.ref), output / name)
                        if name.endswith("png"):
                            cards.append(f'<figure>{name}<img src="{name}"></figure>')
                    (output / "index.html").write_text(
                        '<!doctype html><meta charset="utf-8"><title>在线几何证据</title>'
                        "<style>body{font-family:sans-serif}figure{display:inline-block;width:44%;"
                        "vertical-align:top}img{width:100%}pre{white-space:pre-wrap}</style>"
                        "<h1>真实对话 → 原图检测/复核 → 分割 → 相对深度</h1>"
                        "<p>流程验证，不是准确率实验。掩膜不是缺陷真值；白近黑远，无实际尺寸。</p>"
                        + "".join(cards)
                        + "<pre>"
                        + escape(
                            json.dumps(
                                detail.measurements.model_dump(mode="json"),
                                ensure_ascii=False,
                                indent=2,
                            )
                        )
                        + "</pre>",
                        encoding="utf-8",
                    )
    finally:
        if task_id is not None:
            save("task.json", repo.get_task(task_id).model_dump(mode="json"))
            save("calls.json", [c.model_dump(mode="json") for c in repo.list_calls(task_id)])
            for step in (
                "detect",
                "verify",
                "geometry_source",
                "geometry_segment",
                "geometry_depth",
            ):
                save(step + ".json", repo.load_step(task_id, step))
        save(
            "execution.json",
            {
                "provider_calls": provider_calls,
                "geometry_gpu_calls": gpu_calls,
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
