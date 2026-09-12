"""Real PG/Temporal open-scan paths with local deterministic model substitutes."""

import asyncio
import io
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import psycopg
import pytest
from PIL import Image
from pipe_inspector.config import Settings
from pipe_inspector.contracts.assets import AssetRef
from pipe_inspector.contracts.detection import DetectionBatch
from pipe_inspector.contracts.human_review import FindingReview, HumanReviewSubmission
from pipe_inspector.contracts.inspection import ImageReview
from pipe_inspector.contracts.intake import TaskSubmission
from pipe_inspector.contracts.open_scan import OpenCandidate, OpenDetectionBatch
from pipe_inspector.contracts.planning import CallBudget, PlanStep, TaskPlan
from pipe_inspector.media import MediaStore
from pipe_inspector.storage.human_review import HumanReviewRepository
from pipe_inspector.workflow.activities import ImageActivities
from pipe_inspector.workflow.human_review_activities import HumanReviewActivities
from pipe_inspector.workflow.image import ImageInspectionWorkflow
from pipe_inspector.workflow.open_scan_activities import OpenScanActivities
from pipe_inspector.workflow.start import run_config, video_run_config
from pipe_inspector.workflow.video_activities import VideoActivities
from psycopg import sql
from temporalio import activity
from temporalio.client import Client
from temporalio.testing import ActivityEnvironment
from temporalio.worker import Worker

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("SEWER_RUN_OPEN_SCAN_TEST") != "1",
        reason="opt-in local PostgreSQL/Temporal open-scan test",
    ),
]


class EmptyPrimaryDetector:
    detector_id = "fixture-primary"
    model_version = "primary-v1"
    label_space_version = "five"
    capabilities = SimpleNamespace(supports_batch=False)

    def __init__(self):
        self.calls = 0
        self.unloads = 0

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

    def unload(self):
        self.unloads += 1


class ClearReviewer:
    def __init__(self):
        self.calls = 0

    async def review(self, path, observation):
        self.calls += 1
        return ImageReview(
            image_assessable=True,
            full_image_audited=True,
            candidates=(),
            additional_anomalies=(),
            summary="fixture full-image audit complete",
        )


class FakeOpenDetector:
    detector_id = "yoloe"
    model_version = "open.pt"
    vocabulary_version = "sewer-open-v1"

    def __init__(self, candidates=()):
        self.candidates = tuple(candidates)
        self.calls = 0
        self.unloads = 0
        self.paths = []

    def predict_image(self, path):
        self.calls += 1
        self.paths.append(path)
        return OpenDetectionBatch(
            detector_id=self.detector_id,
            model_version=self.model_version,
            vocabulary_version=self.vocabulary_version,
            width=64,
            height=64,
            candidates=self.candidates,
        )

    def unload(self):
        self.unloads += 1


@pytest.fixture
def case(tmp_path):
    settings = Settings(
        task_queue="test-open-scan-" + uuid4().hex,
        image_review_mode="independent_discovery",
        vlm_model="fixture-vlm",
        vlm_base_url="https://vlm.invalid/v1",
        vlm_api_key="fixture-only",
        open_detector_path="fixtures/open.pt",
        open_text_encoder_path="fixtures/encoder.ts",
    )
    assert settings.temporal_address == "127.0.0.1:7233"
    assert settings.open_scan_config == {
        "version": "open-scan-v1",
        "detector_id": "yoloe",
        "model_version": "open.pt",
        "vocabulary_version": "sewer-open-v1",
        "checkpoint": "fixtures/open.pt",
        "text_encoder": "fixtures/encoder.ts",
        "device": "0",
        "confidence": 0.25,
        "image_size": 640,
        "max_frames": 8,
    }
    schema = "test_open_scan_" + uuid4().hex
    with psycopg.connect(settings.database_dsn.get_secret_value()) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        repo = HumanReviewRepository(settings.database_dsn, schema=schema)
        repo.initialize()
        media = MediaStore(
            tmp_path / "assets",
            max_upload_bytes=1024**2,
            max_pixels=100_000,
            probe_timeout_seconds=5,
        )
        session = repo.create_session()

        image_bytes = io.BytesIO()
        Image.fromarray(
            np.random.default_rng(11).integers(40, 210, (64, 64, 3), dtype=np.uint8)
        ).save(image_bytes, "PNG")
        image_bytes.seek(0)
        image_asset = media.ingest(image_bytes)
        repo.save_asset(image_asset)

        video_path = tmp_path / "short.mp4"
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
                str(video_path),
            ],
            check=True,
        )
        with video_path.open("rb") as stream:
            video_asset = media.ingest(stream)
        repo.save_asset(video_asset)

        def create_task(asset, instruction):
            return repo.create_task(
                TaskSubmission(
                    request_id=uuid4(),
                    session_id=session.session_id,
                    input_asset=asset.ref,
                    instruction=instruction,
                )
            )

        yield SimpleNamespace(
            settings=settings,
            repo=repo,
            media=media,
            image_asset=image_asset,
            video_asset=video_asset,
            create_task=create_task,
        )
    finally:
        with psycopg.connect(settings.database_dsn.get_secret_value()) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


async def _wait_for_stage(handle, stage):
    async with asyncio.timeout(30):
        while await handle.query("current_stage") != stage:
            await asyncio.sleep(0.05)


async def test_image_open_candidate_checkpoint_survives_lost_ack_and_waits_for_human(case):
    task = case.create_task(case.image_asset, "检查图片，并补查开放类别异常")
    task_id = task.snapshot.task_id
    primary = EmptyPrimaryDetector()
    reviewer = ClearReviewer()
    open_detector = FakeOpenDetector(
        (
            OpenCandidate(
                label="PL",
                name="longitudinal crack",
                score=0.82,
                bbox_xyxy=(5, 6, 42, 44),
            ),
        )
    )
    images = ImageActivities(
        case.settings, case.repo, case.media, detector=primary, reviewer=reviewer
    )
    open_scans = OpenScanActivities(
        case.settings,
        case.repo,
        case.media,
        detector=primary,
        open_detector=open_detector,
    )
    reviews = HumanReviewActivities(case.repo)
    # Exercise the archived implementation with explicit pre-retirement pins.
    run = case.repo.ensure_run(
        task_id, run_config(case.settings, primary) | {"open_scan": case.settings.open_scan_config}
    )
    attempts = []

    @activity.defn(name="scan_open_candidate")
    def lose_first_completion(payload):
        attempts.append(activity.info().attempt)
        result = open_scans.scan_open_candidate(payload)
        if activity.info().attempt == 1:
            raise RuntimeError("fixture lost acknowledgement after durable open-scan commit")
        return result

    client = await Client.connect(
        case.settings.temporal_address, namespace=case.settings.temporal_namespace
    )
    handle = None
    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            async with (
                Worker(
                    client,
                    task_queue=case.settings.task_queue,
                    workflows=[ImageInspectionWorkflow],
                    activities=[
                        images.publish_stage,
                        images.verify_image,
                        open_scans.select_open_scan,
                        images.finish_image,
                        reviews.apply_human_review,
                        reviews.cancel_human_review,
                    ],
                    activity_executor=executor,
                ),
                Worker(
                    client,
                    task_queue=case.settings.task_queue + "-gpu",
                    activities=[images.detect_image, lose_first_completion],
                    activity_executor=executor,
                    max_concurrent_activities=1,
                ),
            ):
                handle = await client.start_workflow(
                    ImageInspectionWorkflow.run,
                    run,
                    id=f"test-open-image:{task_id}",
                    task_queue=case.settings.task_queue,
                )
                await _wait_for_stage(handle, "human_review")
                waiting = case.repo.get_task(task_id).snapshot
                assert waiting.runtime_status == "waiting_review"
                assert waiting.inspection_result == "inconclusive"
                assert len(waiting.findings) == 1
                finding = waiting.findings[0]
                assert finding.source == "open_detector"
                assert finding.review_status == "needs_review"
                assert finding.evidence[0].frame_index is None
                plan = case.repo.load_step(task_id, "open_plan")
                assert plan["selected"][0]["frame_index"] is None
                assert case.repo.load_step(task_id, "open_scan:0") is not None

                submission = HumanReviewSubmission(
                    request_id=uuid4(),
                    reviewer="fixture-reviewer",
                    snapshot_updated_at=waiting.updated_at,
                    decisions=(
                        FindingReview(
                            finding_id=finding.finding_id,
                            action="confirm",
                            reason="fixture evidence confirmed",
                        ),
                    ),
                )
                receipt = await handle.execute_update(
                    "submit_review", submission.model_dump(mode="json")
                )
                async with asyncio.timeout(20):
                    result = await handle.result()
        assert result == receipt
        assert result["runtime_status"] == "completed"
        assert result["inspection_result"] == "defects_found"
        assert attempts == [1, 2]
        assert primary.calls == reviewer.calls == open_detector.calls == 1
        assert open_detector.unloads == 1
        final = case.repo.get_task(task_id).snapshot
        assert final.findings[0].confirmed_by == "human"
    finally:
        if handle and (await handle.describe()).status.name == "RUNNING":
            await handle.terminate("isolated test cleanup")


async def _prepare_video(case, *, plan=None):
    task = case.create_task(case.video_asset, "检查短视频中的异常")
    primary = EmptyPrimaryDetector()
    reviewer = ClearReviewer()
    activities = VideoActivities(
        case.settings, case.repo, case.media, detector=primary, reviewer=reviewer
    )
    config = video_run_config(case.settings, primary) | {
        "open_scan": case.settings.open_scan_config,
        "plan": plan.model_dump(mode="json") if plan else None,
        "video_review_budget": 2 if plan else 23,
    }
    config["video_chunk_size"] = 2
    run = case.repo.ensure_run(task.snapshot.task_id, config)
    env = ActivityEnvironment()
    await asyncio.to_thread(env.run, activities.scan_video, run)
    selected = await asyncio.to_thread(env.run, activities.prepare_video, run)
    for frame_index in selected["review_frames"]:
        await env.run(
            activities.verify_video_frame,
            {"run": run, "frame_index": frame_index},
        )
    return task, primary, reviewer, activities, run, env


async def test_video_open_scan_uses_only_materialized_periodic_negative_audit_frame(case):
    task, primary, reviewer, videos, run, env = await _prepare_video(case)
    task_id = task.snapshot.task_id
    evidence = case.repo.load_step(task_id, "video_evidence")
    scan = case.repo.load_step(task_id, "video_scan")
    assert scan["state"]["next_frame"] == primary.calls == 6
    assert len(scan["state"]["audit_frames"]) == 1
    assert len(evidence["frames"]) == 1

    open_detector = FakeOpenDetector()
    open_scans = OpenScanActivities(
        case.settings,
        case.repo,
        case.media,
        detector=primary,
        open_detector=open_detector,
    )
    plan = await asyncio.to_thread(env.run, open_scans.select_open_scan, run)
    assert len(plan["selected"]) == 1
    selected = plan["selected"][0]
    assert selected["reason"] == "negative_audit"
    assert selected["frame_index"] == scan["state"]["audit_frames"][0]["frame_index"]
    assert selected["asset"] == evidence["frames"][0]["asset"]

    await asyncio.to_thread(
        env.run, open_scans.scan_open_candidate, {"run": run, "index": 0}
    )
    assert open_detector.calls == 1
    selected_asset = AssetRef.model_validate(selected["asset"])
    assert open_detector.paths == [case.media.path_for(selected_asset)]
    assert open_detector.paths[0] != case.media.path_for(case.video_asset.ref)
    assert reviewer.calls == 1

    result = await asyncio.to_thread(env.run, videos.finish_video, {"run": run})
    assert result["runtime_status"] == "completed"
    assert result["inspection_result"] == "normal"


async def test_video_open_scan_budget_gap_prevents_normal_result(case):
    plan = TaskPlan(
        open_anomaly_audit=True,
        steps=(
            PlanStep(step_id="detect", action="detect"),
            PlanStep(step_id="verify", action="verify", depends_on=("detect",)),
            PlanStep(step_id="open", action="open_scan", depends_on=("verify",)),
        ),
        budget=CallBudget(max_tool_calls=3, per_event_open_scan=0),
    )
    task, primary, _, videos, run, env = await _prepare_video(case, plan=plan)
    open_detector = FakeOpenDetector()
    open_scans = OpenScanActivities(
        case.settings,
        case.repo,
        case.media,
        detector=primary,
        open_detector=open_detector,
    )

    saved_plan = await asyncio.to_thread(env.run, open_scans.select_open_scan, run)
    assert saved_plan["selected"] == []
    assert saved_plan["incomplete"] is True
    assert saved_plan["deferred_count"] == saved_plan["eligible_count"] == 1
    assert saved_plan["skip_reason"] == "budget_or_evidence_gap"
    assert open_detector.calls == 0

    result = await asyncio.to_thread(env.run, videos.finish_video, {"run": run})
    assert result["runtime_status"] == "waiting_review"
    assert result["inspection_result"] == "inconclusive"
    snapshot = case.repo.get_task(task.snapshot.task_id).snapshot
    assert snapshot.coverage.negative_audit_complete is False
    assert "open_scan_incomplete" in snapshot.waiting_reason
