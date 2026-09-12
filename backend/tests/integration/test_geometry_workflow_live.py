"""Real Temporal/PostgreSQL recovery with deterministic local geometry doubles."""

import asyncio
import io
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from uuid import uuid4

import numpy as np
import psycopg
import pytest
from PIL import Image
from pipe_inspector.config import Settings
from pipe_inspector.contracts.geometry import GeometryOptions
from pipe_inspector.contracts.inspection import ImageReview
from pipe_inspector.contracts.intake import TaskSubmission
from pipe_inspector.media import MediaStore
from pipe_inspector.storage.inspection import InspectionRepository
from pipe_inspector.workflow.activities import ImageActivities
from pipe_inspector.workflow.geometry_activities import GeometryActivities
from pipe_inspector.workflow.geometry_config import build_geometry_config
from pipe_inspector.workflow.image import ImageInspectionWorkflow
from pipe_inspector.workflow.start import run_config
from psycopg import sql
from temporalio import activity
from temporalio.client import Client
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("SEWER_RUN_GEOMETRY_WORKFLOW_TEST") != "1",
        reason="opt-in local Temporal/PostgreSQL geometry recovery test",
    ),
]


class Detector:
    detector_id = "fixture-detector"
    model_version = "fixture-detector-v1"
    label_space_version = "five"

    def __init__(self, trace):
        self.trace = trace

    def predict_image(self, path):
        from pipe_inspector.contracts.detection import DetectionBatch

        self.trace.append("detect")
        return DetectionBatch(
            detector_id=self.detector_id,
            model_version=self.model_version,
            label_space_version=self.label_space_version,
            width=8,
            height=6,
            detections=(),
        )


class Reviewer:
    def __init__(self, trace):
        self.trace = trace

    async def review(self, path, observation):
        self.trace.append("verify")
        return ImageReview(
            image_assessable=True,
            full_image_audited=True,
            candidates=(),
            additional_anomalies=(),
            summary="fixture original review",
        )


class Segmenter:
    model_id = "facebook/sam2.1-hiera-large"
    model_version = "665f8e2ad61cf5f53d65644ff27c8ee525124610"

    def __init__(self, trace):
        self.trace = trace

    def segment_image(self, image, prompt):
        from pipe_inspector.vision.geometry import MaskPrediction

        self.trace.append("segment")
        values = np.zeros((6, 8), dtype=bool)
        values[2:4, 3:6] = True
        return MaskPrediction(values, 0.8)

    def unload(self):
        self.trace.append("unload_segment")


class Depth:
    model_id = "depth-anything/Depth-Anything-V2-Small-hf"
    model_version = "5426e4f0f36572d16453bbda7a8389317b1bef99"
    larger_is = "nearer"

    def __init__(self, trace):
        self.trace = trace

    def predict_depth(self, image):
        self.trace.append("depth")
        return np.tile(np.arange(8, dtype=np.float32), (6, 1))

    def unload(self):
        self.trace.append("unload_depth")


async def test_lost_segment_completion_reuses_checkpoint_across_gpu_worker_replacement(tmp_path):
    settings = Settings(
        asset_root=tmp_path / "assets",
        task_queue="test-geometry-" + uuid4().hex,
        vlm_model="fixture-vlm",
        vlm_base_url="https://vlm.invalid/v1",
        vlm_api_key="fixture-only",
        geometry_models_dir=tmp_path / "models",
    )
    schema = "test_geometry_workflow_" + uuid4().hex
    with psycopg.connect(settings.database_dsn.get_secret_value()) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    handle = None
    try:
        repo = InspectionRepository(settings.database_dsn, schema=schema)
        repo.initialize()
        media = MediaStore(tmp_path / "media", max_upload_bytes=1024**2, max_pixels=1000)
        stream = io.BytesIO()
        Image.new("RGB", (8, 6), (40, 90, 130)).save(stream, "PNG")
        stream.seek(0)
        asset = media.ingest(stream)
        repo.save_asset(asset)
        task = repo.create_task(
            TaskSubmission(
                request_id=uuid4(),
                session_id=repo.create_session().session_id,
                input_asset=asset.ref,
                instruction="fixture relative geometry",
            )
        )
        trace = []
        detector = Detector(trace)
        reviewer = Reviewer(trace)
        config = run_config(settings, detector)
        config["geometry"] = build_geometry_config(
            settings,
            GeometryOptions(operation="relative_measure", roi_xyxy=(2, 1, 7, 5)),
            check_files=False,
        )
        run = repo.ensure_run(task.snapshot.task_id, config)
        originals = ImageActivities(settings, repo, media, detector=detector, reviewer=reviewer)
        geometry = GeometryActivities(
            settings,
            repo,
            media,
            segmenter_factory=lambda checkpoint, device: Segmenter(trace),
            depth_factory=lambda checkpoint, device: Depth(trace),
        )
        checkpointed = Event()
        depth_started = asyncio.Event()
        completion_lost = False

        @activity.defn(name="segment_image")
        def lose_segment_ack(payload):
            nonlocal completion_lost
            saved = geometry.segment_image(payload)
            if not completion_lost:
                completion_lost = True
                checkpointed.set()
                raise ApplicationError("fixture acknowledgement lost", type="fixture_ack_lost")
            return saved

        @activity.defn(name="estimate_relative_depth")
        async def block_depth(payload):
            depth_started.set()
            await activity.wait_for_cancelled()
            raise ApplicationError("fixture worker replaced", type="fixture_worker_replaced")

        client = await Client.connect(
            settings.temporal_address, namespace=settings.temporal_namespace
        )
        with ThreadPoolExecutor(max_workers=4) as executor:
            async with Worker(
                client,
                task_queue=settings.task_queue,
                workflows=[ImageInspectionWorkflow],
                activities=[
                    originals.publish_stage,
                    originals.verify_image,
                    geometry.prepare_image_geometry,
                    originals.finish_image,
                ],
                activity_executor=executor,
            ):
                first_gpu_worker = Worker(
                    client,
                    task_queue=config["gpu_queue"],
                    activities=[
                        originals.detect_image,
                        lose_segment_ack,
                        block_depth,
                    ],
                    activity_executor=executor,
                    max_concurrent_activities=1,
                    graceful_shutdown_timeout=timedelta(seconds=5),
                )
                async with first_gpu_worker:
                    handle = await client.start_workflow(
                        ImageInspectionWorkflow.run,
                        run,
                        id=f"test-geometry:{task.snapshot.task_id}",
                        task_queue=settings.task_queue,
                    )
                    assert await asyncio.to_thread(checkpointed.wait, 20)
                    async with asyncio.timeout(20):
                        await depth_started.wait()
                    winner = repo.load_step(task.snapshot.task_id, "geometry_segment")
                    assert winner is not None
                    await first_gpu_worker.shutdown()
                reopened = InspectionRepository(settings.database_dsn, schema=schema)
                replacement = GeometryActivities(
                    settings,
                    reopened,
                    media,
                    segmenter_factory=lambda checkpoint, device: Segmenter(trace),
                    depth_factory=lambda checkpoint, device: Depth(trace),
                )
                async with Worker(
                    client,
                    task_queue=config["gpu_queue"],
                    activities=[replacement.segment_image, replacement.estimate_relative_depth],
                    activity_executor=executor,
                    max_concurrent_activities=1,
                ):
                    async with asyncio.timeout(40):
                        while True:
                            final = await asyncio.to_thread(
                                reopened.load_step, task.snapshot.task_id, "final"
                            )
                            if final is not None:
                                break
                            await asyncio.sleep(0.1)

        # The blank fixture fails the real quality gate. Do not fake a normal conclusion
        # or wait for a human decision that this recovery test never submits.
        assert final["runtime_status"] == "waiting_review"
        assert final["inspection_result"] == "inconclusive"
        assert "quality_inadequate" in final["waiting_reason"]
        assert trace == [
            "detect",
            "verify",
            "segment",
            "unload_segment",
            "depth",
            "unload_depth",
        ]
        assert reopened.load_step(task.snapshot.task_id, "geometry_segment") == winner
        assert reopened.load_step(task.snapshot.task_id, "geometry_depth") is not None
    finally:
        if handle and (await handle.describe()).status.name == "RUNNING":
            await handle.terminate("test cleanup")
        with psycopg.connect(settings.database_dsn.get_secret_value()) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
