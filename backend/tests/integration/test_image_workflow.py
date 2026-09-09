"""Production image workflow on local Temporal/PG; no paid API or shared-service restart."""

import asyncio
import io
import json
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import httpx
import numpy as np
import psycopg
import pytest
from PIL import Image
from pipe_inspector.agent.api_client import OpenAICompatibleClient
from pipe_inspector.agent.image_review import ImageReviewer
from pipe_inspector.config import Settings
from pipe_inspector.contracts.detection import DetectionBatch
from pipe_inspector.contracts.intake import TaskSubmission
from pipe_inspector.media import MediaStore
from pipe_inspector.storage.inspection import InspectionRepository
from pipe_inspector.workflow.activities import ImageActivities
from pipe_inspector.workflow.image import ImageInspectionWorkflow
from pipe_inspector.workflow.start import run_config
from psycopg import sql
from temporalio.client import Client
from temporalio.worker import Worker

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("SEWER_RUN_IMAGE_WORKFLOW_TEST") != "1",
        reason="opt-in local Temporal/PG workflow test; does not restart shared services",
    ),
]


class EmptyDetector:
    detector_id, model_version, label_space_version = "fixture", "v1", "five"

    def __init__(self):
        self.calls = 0
        self.entered, self.release = Event(), Event()

    def predict_image(self, path):
        self.calls += 1
        self.entered.set()
        if not self.release.wait(timeout=20):
            raise RuntimeError("test detector gate timed out")
        return DetectionBatch(
            detector_id=self.detector_id,
            model_version=self.model_version,
            label_space_version=self.label_space_version,
            width=64,
            height=64,
            detections=(),
        )


@pytest.fixture
def case(tmp_path):
    settings = Settings(
        task_queue="test-image-" + uuid4().hex,
        vlm_model="fixture-vlm",
        vlm_base_url="https://vlm.invalid/v1",
        vlm_api_key="fixture-only",
    )
    assert settings.temporal_address == "127.0.0.1:7233"
    assert settings.temporal_namespace == "default"
    schema = "test_image_" + uuid4().hex
    with psycopg.connect(settings.database_dsn.get_secret_value()) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        repo = InspectionRepository(settings.database_dsn, schema=schema)
        repo.initialize()
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
        task = repo.create_task(
            TaskSubmission(
                request_id=uuid4(),
                session_id=repo.create_session().session_id,
                input_asset=asset.ref,
                instruction="检查这张图片是否正常",
            )
        )
        yield settings, repo, media, task
    finally:
        with psycopg.connect(settings.database_dsn.get_secret_value()) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


async def test_worker_restart_resumes_after_detection_and_retries_only_vlm(case):
    settings, repo, media, task = case
    detector = EmptyDetector()
    attempts = []

    def provider(request):
        attempts.append(json.loads(request.content))
        if len(attempts) == 1:
            return httpx.Response(429)
        review = {
            "image_assessable": True,
            "full_image_audited": True,
            "candidates": [],
            "additional_anomalies": [],
            "summary": "全图未见缺陷",
        }
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(review)}}]
            },
        )

    reviewer = ImageReviewer(
        OpenAICompatibleClient(
            settings.vlm_base_url,
            settings.vlm_model,
            settings.vlm_api_key,
            transport=httpx.MockTransport(provider),
        )
    )
    activities = ImageActivities(settings, repo, media, detector=detector, reviewer=reviewer)
    task_id = task.snapshot.task_id
    run = repo.ensure_run(task_id, run_config(settings, detector))
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    handle = None
    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            async with Worker(
                client,
                task_queue=settings.task_queue + "-gpu",
                activities=[activities.detect_image],
                activity_executor=executor,
                max_concurrent_activities=1,
            ):
                async with Worker(
                    client,
                    task_queue=settings.task_queue,
                    workflows=[ImageInspectionWorkflow],
                    activities=[
                        activities.publish_stage,
                        activities.verify_image,
                        activities.finish_image,
                    ],
                    activity_executor=executor,
                ):
                    handle = await client.start_workflow(
                        ImageInspectionWorkflow.run,
                        run,
                        id=f"test-image:{task_id}",
                        task_queue=settings.task_queue,
                    )
                    assert await asyncio.to_thread(detector.entered.wait, 15)
                # CPU worker has stopped while the GPU call is still running.
                detector.release.set()
                async with asyncio.timeout(15):
                    while repo.load_step(task_id, "detect") is None:
                        await asyncio.sleep(0.05)
            # A new worker replays real Temporal history; no GPU worker is available now.
            async with Worker(
                client,
                task_queue=settings.task_queue,
                workflows=[ImageInspectionWorkflow],
                activities=[
                    activities.publish_stage,
                    activities.verify_image,
                    activities.finish_image,
                ],
                activity_executor=executor,
            ):
                async with asyncio.timeout(40):
                    result = await handle.result()
        assert result["runtime_status"] == "completed"
        assert result["inspection_result"] == "normal"
        assert detector.calls == 1
        assert len(attempts) == 2
        calls = repo.list_calls(task_id)
        assert [(call.step_id, call.attempt, call.status) for call in calls] == [
            ("detect", 1, "succeeded"),
            ("verify", 1, "failed"),
            ("verify", 2, "succeeded"),
        ]
        assert repo.get_task(task_id).snapshot.retry_count == 1
        assert repo.get_task(task_id).instruction == task.instruction
    finally:
        detector.release.set()
        if handle and (await handle.describe()).status.name == "RUNNING":
            await handle.terminate("test cleanup")


async def test_http_start_reconciles_duplicate_and_exposes_saved_result_and_calls(case):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from pipe_inspector.api.app import create_app
    from pipe_inspector.contracts.inspection import ImageReview

    settings, repo, media, task = case
    detector = EmptyDetector()
    detector.release.set()
    reviewer = SimpleNamespace(
        review=AsyncMock(
            return_value=ImageReview(
                image_assessable=True,
                full_image_audited=True,
                candidates=(),
                additional_anomalies=(),
                summary="全图审核完成",
            )
        )
    )
    activities = ImageActivities(settings, repo, media, detector=detector, reviewer=reviewer)
    task_id = task.snapshot.task_id
    repo.ensure_run(task_id, run_config(settings, detector))
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    handle = client.get_workflow_handle(f"inspection:{task_id}")
    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            async with (
                Worker(
                    client,
                    task_queue=settings.task_queue,
                    workflows=[ImageInspectionWorkflow],
                    activities=[
                        activities.publish_stage,
                        activities.verify_image,
                        activities.finish_image,
                    ],
                    activity_executor=executor,
                ),
                Worker(
                    client,
                    task_queue=settings.task_queue + "-gpu",
                    activities=[activities.detect_image],
                    activity_executor=executor,
                    max_concurrent_activities=1,
                ),
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=create_app(settings, repository=repo)),
                    base_url="http://test",
                ) as http,
            ):
                first, replay = await asyncio.gather(
                    *(http.post(f"/api/tasks/{task_id}/start") for _ in range(2))
                )
                assert first.status_code == replay.status_code == 202
                assert first.json()["snapshot"]["task_id"] == replay.json()["snapshot"]["task_id"]
                async with asyncio.timeout(30):
                    await handle.result()
                saved = await http.get(f"/api/tasks/{task_id}")
                calls = await http.get(f"/api/tasks/{task_id}/calls")
                assert saved.json()["snapshot"]["inspection_result"] == "normal"
                assert saved.json()["instruction"] == task.instruction
                assert calls.status_code == 200
                assert [call["status"] for call in calls.json()] == ["succeeded", "succeeded"]
                assert detector.calls == 1
    finally:
        if (await handle.describe()).status.name == "RUNNING":
            await handle.terminate("test cleanup")
