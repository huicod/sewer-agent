"""Explicit trusted-checkpoint smoke through the production workflow (no external VLM)."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from pipe_inspector.contracts.intake import TaskSubmission
from pipe_inspector.media import MediaStore
from pipe_inspector.vision.detector import UltralyticsDetector
from pipe_inspector.workflow.activities import ImageActivities
from pipe_inspector.workflow.image import ImageInspectionWorkflow
from pipe_inspector.workflow.start import run_config
from temporalio.client import Client
from temporalio.worker import Worker
from test_image_workflow import case  # noqa: F401 - shared isolated PG/assets fixture

pytestmark = [
    pytest.mark.integration,
    pytest.mark.gpu,
    pytest.mark.skipif(
        os.getenv("SEWER_RUN_IMAGE_GPU_TEST") != "1",
        reason="explicit opt-in: load only the already authorized trusted checkpoint on GPU",
    ),
]


async def test_trusted_checkpoint_runs_through_durable_image_workflow(case):  # noqa: F811
    settings, repo, media, _ = case
    media = MediaStore(media.root, max_upload_bytes=1024**2, max_pixels=1_000_000)
    expected = Path("yolov26/experiments/sewer2403_5class_yolo26s_img960_e100/weights/best.pt")
    assert settings.detector_path == expected
    settings = settings.model_copy(
        update={"vlm_base_url": None, "vlm_model": None, "vlm_api_key": None}
    )
    with Path("var/fixtures/sewer2403-sample.jpg").open("rb") as source:
        asset = media.ingest(source)
    repo.save_asset(asset)
    task = repo.create_task(
        TaskSubmission(
            request_id=uuid4(),
            session_id=repo.create_session().session_id,
            input_asset=asset.ref,
            instruction="检查真实管网图片的五类缺陷",
        )
    )
    detector = UltralyticsDetector(expected, device=settings.detector_device)
    activities = ImageActivities(settings, repo, media, detector=detector)
    run = repo.ensure_run(task.snapshot.task_id, run_config(settings, detector))
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    handle = None
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
            ):
                handle = await client.start_workflow(
                    ImageInspectionWorkflow.run,
                    run,
                    id=f"test-image-gpu:{task.snapshot.task_id}",
                    task_queue=settings.task_queue,
                )
                async with asyncio.timeout(120):
                    result = await handle.result()
        observed = repo.load_step(task.snapshot.task_id, "detect")
        assert observed["detection"]["width"] == 720
        assert observed["detection"]["height"] == 576
        assert any(
            item["label"] == "AJ" and item["score"] > 0.9
            for item in observed["detection"]["detections"]
        )
        assert result["runtime_status"] in {"completed", "partial"}
        assert result["inspection_result"] != "normal"
        assert result["findings"]
        assert repo.list_calls(task.snapshot.task_id)[0].status == "succeeded"
        print(
            {
                "runtime_status": result["runtime_status"],
                "inspection_result": result["inspection_result"],
                "labels": [item["canonical_label"] for item in result["findings"]],
                "tool_statuses": [
                    (call.step_id, call.status) for call in repo.list_calls(task.snapshot.task_id)
                ],
            }
        )
    finally:
        if handle and (await handle.describe()).status.name == "RUNNING":
            await handle.terminate("test cleanup")
