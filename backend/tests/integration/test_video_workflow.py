"""Real local PG/Temporal and FFmpeg, deterministic injected detector/API faults."""

import asyncio
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import psycopg
import pytest
from pipe_inspector.config import Settings
from pipe_inspector.contracts.detection import DetectionBatch
from pipe_inspector.contracts.inspection import ImageReview
from pipe_inspector.contracts.intake import TaskSubmission
from pipe_inspector.media import MediaStore
from pipe_inspector.storage.video import VideoRepository
from pipe_inspector.workflow.start import video_run_config
from pipe_inspector.workflow.video import VideoInspectionWorkflow
from pipe_inspector.workflow.video_activities import VideoActivities
from psycopg import sql
from temporalio import activity
from temporalio.client import Client
from temporalio.testing import ActivityEnvironment
from temporalio.worker import Worker

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("SEWER_RUN_VIDEO_WORKFLOW_TEST") != "1",
        reason="opt-in local PG/Temporal video test",
    ),
]


class Detector:
    detector_id, model_version, label_space_version = "fixture", "v1", "five"
    capabilities = SimpleNamespace(supports_batch=False)

    def __init__(self):
        self.calls = 0

    def predict_image(self, path):
        self.calls += 1
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
        task_queue="test-video-" + uuid4().hex,
        vlm_model="fixture",
        vlm_base_url="https://vlm.invalid/v1",
        vlm_api_key="fixture-only",
    )
    assert settings.temporal_address == "127.0.0.1:7233"
    schema = "test_video_workflow_" + uuid4().hex
    with psycopg.connect(settings.database_dsn.get_secret_value()) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        repo = VideoRepository(settings.database_dsn, schema=schema)
        repo.initialize()
        clip = tmp_path / "six.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=s=64x64:r=5:d=1.2",
                "-c:v",
                "libx264",
                str(clip),
            ],
            check=True,
        )
        media = MediaStore(tmp_path / "assets")
        with clip.open("rb") as stream:
            asset = media.ingest(stream)
        repo.save_asset(asset)
        task = repo.create_task(
            TaskSubmission(
                request_id=uuid4(),
                session_id=repo.create_session().session_id,
                input_asset=asset.ref,
                instruction="检查整个视频",
            )
        )
        detector = Detector()
        reviewer = SimpleNamespace(
            review=AsyncMock(
                return_value=ImageReview(
                    image_assessable=True,
                    full_image_audited=True,
                    candidates=(),
                    additional_anomalies=(),
                    summary="完成全图复检",
                )
            )
        )
        activities = VideoActivities(settings, repo, media, detector=detector, reviewer=reviewer)
        config = video_run_config(settings, detector)
        config["video_chunk_size"] = 2
        run = repo.ensure_run(task.snapshot.task_id, config)
        yield settings, repo, media, detector, reviewer, activities, run
    finally:
        with psycopg.connect(settings.database_dsn.get_secret_value()) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


async def test_checkpoint_resume_materialization_and_cached_review(case, monkeypatch):
    _, repo, media, detector, reviewer, activities, run = case
    task_id = UUID(run["task_id"])
    env = ActivityEnvironment()
    commit = repo.commit_video_chunk

    def fail_after_commit(task_id, observations, **kwargs):
        state = commit(task_id, observations, **kwargs)
        if state.next_frame == 2:
            raise psycopg.OperationalError("simulated lost acknowledgement")
        return state

    monkeypatch.setattr(repo, "commit_video_chunk", fail_after_commit)
    with pytest.raises(psycopg.OperationalError):
        await asyncio.to_thread(env.run, activities.scan_video, run)
    assert repo.load_video_state(task_id).next_frame == detector.calls == 2
    monkeypatch.setattr(repo, "commit_video_chunk", commit)
    await asyncio.to_thread(env.run, activities.scan_video, run)
    assert detector.calls == 6
    await asyncio.to_thread(env.run, activities.scan_video, run)
    assert detector.calls == 6
    selected = await asyncio.to_thread(env.run, activities.prepare_video, run)
    assert selected["review_frames"]
    assert selected == await asyncio.to_thread(env.run, activities.prepare_video, run)
    saved = repo.load_step(task_id, "video_evidence")
    for item in saved["frames"]:
        from pipe_inspector.contracts.assets import AssetRef

        ref = AssetRef.model_validate(item["asset"])
        assert repo.get_asset(ref).parent == repo.get_task(task_id).input_asset
        assert media.path_for(ref).is_file()
    for index in selected["review_frames"]:
        payload = {"run": run, "frame_index": index}
        await env.run(activities.verify_video_frame, payload)
        await env.run(activities.verify_video_frame, payload)
    assert reviewer.review.await_count == len(selected["review_frames"])
    result = await asyncio.to_thread(env.run, activities.finish_video, {"run": run})
    snapshot = repo.get_task(task_id).snapshot
    assert snapshot.runtime_status == "completed"
    assert snapshot.inspection_result == "normal"
    assert snapshot.processed_frames == 6 and snapshot.processed_chunks == 3
    assert not snapshot.can_cancel
    assert result == await asyncio.to_thread(env.run, activities.finish_video, {"run": run})


async def test_video_review_failure_waits_and_preserves_later_human_terminal(case):
    from pipe_inspector.contracts.tasks import TaskSnapshot

    _, repo, _, _, _, activities, run = case
    task_id = UUID(run["task_id"])
    env = ActivityEnvironment()
    await asyncio.to_thread(env.run, activities.scan_video, run)
    await asyncio.to_thread(env.run, activities.prepare_video, run)
    result = await asyncio.to_thread(
        env.run, activities.finish_video, {"run": run, "error": "review_failed"}
    )
    assert result["runtime_status"] == "waiting_review"
    before = repo.get_task(task_id).snapshot
    assert before.stage == "human_review" and before.can_cancel
    after = TaskSnapshot.model_validate(
        before.model_dump()
        | {
            "runtime_status": "partial",
            "stage": "finished",
            "can_cancel": False,
            "overall_progress": 1,
            "waiting_reason": "人工确认覆盖不足",
        }
    )
    repo.update_snapshot(after)
    result = await asyncio.to_thread(env.run, activities.finish_video, {"run": run})
    assert result["runtime_status"] == "partial"
    assert repo.get_task(task_id).snapshot == after


async def test_real_workflow_worker_replacement_after_scan(case):
    settings, repo, _, detector, reviewer, activities, run = case
    client = await Client.connect(settings.temporal_address)
    handle = None
    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            async with Worker(
                client,
                task_queue=settings.task_queue + "-gpu",
                activities=[activities.scan_video],
                activity_executor=executor,
            ):
                async with Worker(
                    client,
                    task_queue=settings.task_queue,
                    workflows=[VideoInspectionWorkflow],
                    activities=[],
                    activity_executor=executor,
                ):
                    handle = await client.start_workflow(
                        VideoInspectionWorkflow.run,
                        run,
                        id="test-video:" + run["task_id"],
                        task_queue=settings.task_queue,
                    )
                    async with asyncio.timeout(20):
                        while repo.load_step(UUID(run["task_id"]), "video_scan") is None:
                            await asyncio.sleep(0.05)
                        # The DB checkpoint precedes the Temporal Activity acknowledgement.
                        # This case removes the GPU worker only AFTER the scan is acknowledged;
                        # lost-ack recovery separately tests a replacement GPU worker.
                        while await handle.query("current_stage") != "aggregating":
                            await asyncio.sleep(0.05)
            # CPU replacement replays real history; GPU worker is now absent.
            async with Worker(
                client,
                task_queue=settings.task_queue,
                workflows=[VideoInspectionWorkflow],
                activities=[
                    activities.prepare_video,
                    activities.verify_video_frame,
                    activities.finish_video,
                ],
                activity_executor=executor,
            ):
                async with asyncio.timeout(30):
                    result = await handle.result()
        assert result["runtime_status"] == "completed"
        assert detector.calls == 6
        assert reviewer.review.await_count > 0
        assert repo.get_task(UUID(run["task_id"])).snapshot.processed_frames == 6
    finally:
        if handle and (await handle.describe()).status.name == "RUNNING":
            await handle.terminate("isolated test cleanup")


async def test_cancel_signal_stops_queued_scan_and_preserves_terminal(case):
    settings, repo, _, detector, _, activities, run = case
    client = await Client.connect(settings.temporal_address)
    handle = None
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            async with Worker(
                client,
                task_queue=settings.task_queue,
                workflows=[VideoInspectionWorkflow],
                activities=[activities.finish_video],
                activity_executor=executor,
            ):
                handle = await client.start_workflow(
                    VideoInspectionWorkflow.run,
                    run,
                    id="test-cancel:" + run["task_id"],
                    task_queue=settings.task_queue,
                )
                assert await handle.execute_update("request_cancel") is True
                async with asyncio.timeout(20):
                    result = await handle.result()
        assert result["runtime_status"] == "cancelled"
        assert detector.calls == 0
        assert repo.get_task(UUID(run["task_id"])).snapshot.inspection_result == "inconclusive"
    finally:
        if handle and (await handle.describe()).status.name == "RUNNING":
            await handle.terminate("isolated test cleanup")


async def test_cancellation_during_final_commit_is_explicitly_refused(case):
    settings, repo, _, _, _, activities, run = case
    client = await Client.connect(settings.temporal_address)
    entered, release = Event(), Event()

    @activity.defn(name="finish_video")
    def finish(payload):
        entered.set()
        if not release.wait(20):
            raise RuntimeError("test release missing")
        return activities.finish_video(payload)

    handle = None
    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            async with (
                Worker(
                    client,
                    task_queue=settings.task_queue + "-gpu",
                    activities=[activities.scan_video],
                    activity_executor=executor,
                ),
                Worker(
                    client,
                    task_queue=settings.task_queue,
                    workflows=[VideoInspectionWorkflow],
                    activities=[activities.prepare_video, activities.verify_video_frame, finish],
                    activity_executor=executor,
                ),
            ):
                handle = await client.start_workflow(
                    VideoInspectionWorkflow.run,
                    run,
                    id="test-finalize:" + run["task_id"],
                    task_queue=settings.task_queue,
                )
                assert await asyncio.to_thread(entered.wait, 20)
                async with asyncio.timeout(5):
                    assert await handle.execute_update("request_cancel") is False
                release.set()
                async with asyncio.timeout(20):
                    assert (await handle.result())["runtime_status"] == "completed"
        assert not repo.get_task(UUID(run["task_id"])).snapshot.can_cancel
    finally:
        release.set()
        if handle and (await handle.describe()).status.name == "RUNNING":
            await handle.terminate("isolated test cleanup")
