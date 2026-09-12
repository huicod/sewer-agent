"""Real Temporal/PG enhancement recovery with deterministic local tool fixtures."""

import asyncio
import io
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Event
from uuid import uuid4

import numpy as np
import psycopg
import pytest
from PIL import Image
from pipe_inspector.config import Settings
from pipe_inspector.contracts.assets import AssetRef
from pipe_inspector.contracts.detection import Detection, DetectionBatch
from pipe_inspector.contracts.enhancement import EnhancementOptions, EnhancementReview
from pipe_inspector.contracts.human_review import FindingReview, HumanReviewSubmission
from pipe_inspector.contracts.inspection import (
    CandidateReview,
    ImageObservation,
    ImageReview,
    QualitySummary,
)
from pipe_inspector.contracts.intake import TaskSubmission
from pipe_inspector.contracts.tasks import CoverageSummary, TaskSnapshot
from pipe_inspector.media import MediaStore
from pipe_inspector.storage.human_review import HumanReviewRepository
from pipe_inspector.workflow.activities import ImageActivities
from pipe_inspector.workflow.enhancement_activities import EnhancementActivities
from pipe_inspector.workflow.enhancement_config import build_enhancement_config
from pipe_inspector.workflow.human_review_activities import HumanReviewActivities
from pipe_inspector.workflow.image import ImageInspectionWorkflow
from pipe_inspector.workflow.start import InspectionStarter, run_config
from pipe_inspector.workflow.supplement import ImageSupplementWorkflow
from pipe_inspector.workflow.supplement_activities import SupplementActivities
from psycopg import sql
from temporalio import activity
from temporalio.client import Client
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("SEWER_RUN_ENHANCEMENT_WORKFLOW_TEST") != "1",
        reason="opt-in local Temporal/PG enhancement recovery test",
    ),
]


class FixtureDetector:
    detector_id = "fixture-detector"
    model_version = "fixture-detector-v1"
    label_space_version = "five"

    def __init__(self, trace, detections=()):
        self.trace = trace
        self.detections = detections

    def predict_image(self, path):
        self.trace.append("detect")
        with Image.open(path) as image:
            width, height = image.size
        return DetectionBatch(
            detector_id=self.detector_id,
            model_version=self.model_version,
            label_space_version=self.label_space_version,
            width=width,
            height=height,
            detections=self.detections,
        )


class FixtureReviewer:
    def __init__(self, trace, *, conflict=False):
        self.trace = trace
        self.conflict = conflict

    async def review(self, *args):
        if len(args) == 2:
            self.trace.append("verify")
            observation = args[1]
            candidates = (
                CandidateReview(index=0, verdict="accept", reason="fixture accepts candidate"),
            ) if observation.detection.detections else ()
            return ImageReview(
                image_assessable=True,
                full_image_audited=True,
                candidates=candidates,
                additional_anomalies=(),
                summary="fixture original-image review",
            )
        if len(args) == 4:
            self.trace.append("enhancement_review")
            baseline = args[3]
            return EnhancementReview(
                quality_effect="unchanged",
                conflicting_finding_ids=(baseline.findings[0].finding_id,)
                if self.conflict
                else (),
                summary="fixture original/derived comparison",
            )
        raise AssertionError("unexpected reviewer signature")


class FixtureModel:
    kind = "dehaze"
    model_id = "dehazeformer-mct"
    model_version = "dehazeformer-mct-8e0b26c7.pth"
    scale = 1

    def __init__(self, trace):
        self.trace = trace

    def enhance_image(self, image):
        self.trace.append("enhance")
        return image.copy()

    def unload(self):
        return None


class FixtureModelFactory:
    def __init__(self, trace):
        self.trace = trace
        self.calls = 0

    def __call__(self, kind, checkpoint, *, device):
        self.calls += 1
        return FixtureModel(self.trace)


@pytest.fixture
def live_case(tmp_path):
    settings = Settings(
        asset_root=tmp_path,
        task_queue="test-enhancement-" + uuid4().hex,
        vlm_model="fixture-vlm",
        vlm_base_url="https://vlm.invalid/v1",
        vlm_api_key="fixture-only",
        enhancement_models_dir=tmp_path / "models",
    )
    assert settings.temporal_address == "127.0.0.1:7233"
    assert settings.temporal_namespace == "default"
    schema = "test_enhancement_workflow_" + uuid4().hex
    with psycopg.connect(settings.database_dsn.get_secret_value()) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        repo = HumanReviewRepository(settings.database_dsn, schema=schema)
        repo.initialize()
        media = MediaStore(
            tmp_path / "media",
            max_upload_bytes=1024**2,
            max_pixels=100_000,
            probe_timeout_seconds=5,
        )
        stream = io.BytesIO()
        Image.fromarray(
            np.random.default_rng(7).integers(30, 220, (64, 64, 3), dtype=np.uint8)
        ).save(stream, "PNG")
        stream.seek(0)
        asset = media.ingest(stream)
        repo.save_asset(asset)
        task = repo.create_task(
            TaskSubmission(
                request_id=uuid4(),
                session_id=repo.create_session().session_id,
                input_asset=asset.ref,
                instruction="run the enhancement recovery fixture",
            )
        )
        yield settings, repo, media, task
    finally:
        with psycopg.connect(settings.database_dsn.get_secret_value()) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )


async def test_lost_enhance_ack_replays_checkpoint_after_worker_replacement(live_case):
    settings, repo, media, task = live_case
    trace = []
    detector = FixtureDetector(trace)
    reviewer = FixtureReviewer(trace)
    model_factory = FixtureModelFactory(trace)
    config = run_config(settings, detector)
    config["enhancement"] = build_enhancement_config(
        settings, EnhancementOptions(kind="dehaze"), check_files=False
    )
    task_id = task.snapshot.task_id
    run = repo.ensure_run(task_id, config)
    image_activities = ImageActivities(
        settings, repo, media, detector=detector, reviewer=reviewer
    )
    enhancement_activities = EnhancementActivities(
        settings,
        repo,
        media,
        reviewer=reviewer,
        model_factory=model_factory,
    )
    checkpointed = Event()

    @activity.defn(name="enhance_image")
    def lose_enhance_ack(run):
        enhancement_activities.enhance_image(run)
        checkpointed.set()
        raise ApplicationError("fixture acknowledgement lost", type="fixture_ack_lost")

    client = await Client.connect(
        settings.temporal_address, namespace=settings.temporal_namespace
    )
    handle = None
    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            async with (
                Worker(
                    client,
                    task_queue=settings.task_queue,
                    workflows=[ImageInspectionWorkflow],
                    activities=[
                        image_activities.publish_stage,
                        image_activities.verify_image,
                        enhancement_activities.prepare_image_enhancement,
                        enhancement_activities.review_image_enhancement,
                        image_activities.finish_image,
                    ],
                    activity_executor=executor,
                ),
                Worker(
                    client,
                    task_queue=config["gpu_queue"],
                    activities=[image_activities.detect_image, lose_enhance_ack],
                    activity_executor=executor,
                    max_concurrent_activities=1,
                ),
            ):
                handle = await client.start_workflow(
                    ImageInspectionWorkflow.run,
                    run,
                    id=f"test-enhancement:{task_id}",
                    task_queue=settings.task_queue,
                )
                assert await asyncio.to_thread(checkpointed.wait, 20)

            winner = repo.load_step(task_id, "enhance")
            assert winner is not None
            winner_ref = AssetRef.model_validate(winner["output_asset"]["ref"])
            assert model_factory.calls == 1
            assert media.path_for(winner_ref).is_file()

            reopened = HumanReviewRepository(settings.database_dsn, schema=repo._schema)
            replaced_image = ImageActivities(
                settings, reopened, media, detector=detector, reviewer=reviewer
            )
            replaced_enhancement = EnhancementActivities(
                settings,
                reopened,
                media,
                reviewer=reviewer,
                model_factory=model_factory,
            )
            async with (
                Worker(
                    client,
                    task_queue=settings.task_queue,
                    workflows=[ImageInspectionWorkflow],
                    activities=[
                        replaced_image.publish_stage,
                        replaced_image.verify_image,
                        replaced_enhancement.prepare_image_enhancement,
                        replaced_enhancement.review_image_enhancement,
                        replaced_image.finish_image,
                    ],
                    activity_executor=executor,
                ),
                Worker(
                    client,
                    task_queue=config["gpu_queue"],
                    activities=[
                        replaced_image.detect_image,
                        replaced_enhancement.enhance_image,
                    ],
                    activity_executor=executor,
                    max_concurrent_activities=1,
                ),
            ):
                async with asyncio.timeout(40):
                    result = await handle.result()

        assert result["runtime_status"] == "completed"
        assert result["inspection_result"] == "normal"
        assert trace == ["detect", "verify", "enhance", "enhancement_review"]
        assert model_factory.calls == 1
        assert reopened.load_step(task_id, "enhance") == winner
        assert reopened.get_asset(winner_ref).ref == winner_ref
        enhanced = reopened.load_step(task_id, "enhance")
        assert enhanced["output_asset"]["ref"] == winner_ref.model_dump(mode="json")
        calls = reopened.list_calls(task_id)
        assert [(call.step_id, str(call.status)) for call in calls] == [
            ("detect", "succeeded"),
            ("enhance", "succeeded"),
            ("enhancement_review", "succeeded"),
            ("verify", "succeeded"),
        ]
    finally:
        if handle and (await handle.describe()).status.name == "RUNNING":
            await handle.terminate("test cleanup")


async def test_enhancement_conflict_waits_for_human_and_preserves_detector_box(live_case):
    settings, repo, media, task = live_case
    original_box = (8.0, 9.0, 20.0, 24.0)
    trace = []
    detector = FixtureDetector(
        trace,
        detections=(
            Detection(
                label="AJ",
                native_label="AJ",
                score=0.9,
                bbox_xyxy=original_box,
            ),
        ),
    )
    reviewer = FixtureReviewer(trace, conflict=True)
    model_factory = FixtureModelFactory(trace)
    config = run_config(settings, detector)
    config["enhancement"] = build_enhancement_config(
        settings, EnhancementOptions(kind="dehaze"), check_files=False
    )
    task_id = task.snapshot.task_id
    run = repo.ensure_run(task_id, config)
    image_activities = ImageActivities(
        settings, repo, media, detector=detector, reviewer=reviewer
    )
    enhancement_activities = EnhancementActivities(
        settings,
        repo,
        media,
        reviewer=reviewer,
        model_factory=model_factory,
    )
    human_activities = HumanReviewActivities(repo)
    client = await Client.connect(
        settings.temporal_address, namespace=settings.temporal_namespace
    )
    handle = None
    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            async with (
                Worker(
                    client,
                    task_queue=settings.task_queue,
                    workflows=[ImageInspectionWorkflow],
                    activities=[
                        image_activities.publish_stage,
                        image_activities.verify_image,
                        enhancement_activities.prepare_image_enhancement,
                        enhancement_activities.review_image_enhancement,
                        image_activities.finish_image,
                        human_activities.apply_human_review,
                        human_activities.cancel_human_review,
                    ],
                    activity_executor=executor,
                ),
                Worker(
                    client,
                    task_queue=config["gpu_queue"],
                    activities=[
                        image_activities.detect_image,
                        enhancement_activities.enhance_image,
                    ],
                    activity_executor=executor,
                    max_concurrent_activities=1,
                ),
            ):
                handle = await client.start_workflow(
                    ImageInspectionWorkflow.run,
                    run,
                    id=f"test-enhancement-conflict:{task_id}",
                    task_queue=settings.task_queue,
                )
                async with asyncio.timeout(30):
                    while await handle.query("current_stage") != "human_review":
                        await asyncio.sleep(0.05)
                waiting = repo.get_task(task_id).snapshot
                assert waiting.runtime_status == "waiting_review"
                assert waiting.inspection_result == "inconclusive"
                assert len(waiting.findings) == 1
                finding = waiting.findings[0]
                assert finding.review_status == "needs_review"
                assert finding.evidence[0].region.bbox_xyxy == original_box

                submission = HumanReviewSubmission(
                    request_id=uuid4(),
                    reviewer="fixture-reviewer",
                    snapshot_updated_at=waiting.updated_at,
                    decisions=(
                        FindingReview(
                            finding_id=finding.finding_id,
                            action="confirm",
                            reason="fixture human confirms original detector evidence",
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
        final = repo.get_task(task_id).snapshot
        assert final.findings[0].evidence[0].region.bbox_xyxy == original_box
        assert final.findings[0].confirmed_by == "human"
        assert trace == ["detect", "verify", "enhance", "enhancement_review"]
        assert model_factory.calls == 1
    finally:
        if handle and (await handle.describe()).status.name == "RUNNING":
            await handle.terminate("test cleanup")


async def test_linked_supplement_enhances_frozen_parent_without_original_tools(live_case):
    settings, repo, media, parent = live_case
    detector = FixtureDetector([])
    parent_id = parent.snapshot.task_id
    observation = ImageObservation(
        detection=DetectionBatch(
            detector_id=detector.detector_id,
            model_version=detector.model_version,
            label_space_version=detector.label_space_version,
            width=64,
            height=64,
            detections=(),
        ),
        quality=QualitySummary(
            mean_luminance=120,
            contrast=40,
            sharpness=500,
            adequate=True,
        ),
    )
    parent_review = ImageReview(
        image_assessable=True,
        full_image_audited=True,
        candidates=(),
        additional_anomalies=(),
        summary="frozen parent review",
    )
    repo.ensure_run(parent_id, run_config(settings, detector))
    repo.save_step(parent_id, "detect", observation.model_dump(mode="json"))
    repo.save_step(
        parent_id,
        "verify",
        {"review": parent_review.model_dump(mode="json"), "skipped": False},
    )
    parent_snapshot = TaskSnapshot.model_validate(
        parent.snapshot.model_dump()
        | {
            "runtime_status": "completed",
            "inspection_result": "normal",
            "stage": "finished",
            "stage_progress": 1,
            "overall_progress": 1,
            "processed_frames": 1,
            "coverage": CoverageSummary(
                sufficient_coverage=True,
                sufficient_quality=True,
                negative_audit_complete=True,
            ),
            "updated_at": datetime.now(timezone.utc),
        }
    )
    frozen_parent = repo.update_snapshot(parent_snapshot)
    frozen_json = frozen_parent.model_dump(mode="json")

    settings.enhancement_models_dir.mkdir(parents=True)
    (settings.enhancement_models_dir / FixtureModel.model_version).write_bytes(b"fixture")
    starter = InspectionStarter(settings, repo)
    child = await starter.admit_supplement(
        parent_id,
        TaskSubmission(
            request_id=uuid4(),
            session_id=parent.session_id,
            input_asset=parent.input_asset,
            instruction="enhance only the frozen supplement evidence",
        ),
        enhancement=EnhancementOptions(kind="dehaze"),
    )
    child_id = child.snapshot.task_id
    run = repo.get_run(child_id)
    assert run is not None
    trace = []
    reviewer = FixtureReviewer(trace)
    model_factory = FixtureModelFactory(trace)
    supplement_activities = SupplementActivities(
        settings, repo, media, detector=detector, reviewer=reviewer
    )
    enhancement_activities = EnhancementActivities(
        settings,
        repo,
        media,
        reviewer=reviewer,
        model_factory=model_factory,
    )
    client = await Client.connect(
        settings.temporal_address, namespace=settings.temporal_namespace
    )
    handle = None
    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            async with (
                Worker(
                    client,
                    task_queue=settings.task_queue,
                    workflows=[ImageSupplementWorkflow],
                    activities=[
                        enhancement_activities.prepare_image_enhancement,
                        enhancement_activities.review_image_enhancement,
                        supplement_activities.finish_supplement,
                    ],
                    activity_executor=executor,
                ),
                Worker(
                    client,
                    task_queue=run["config"]["gpu_queue"],
                    activities=[enhancement_activities.enhance_image],
                    activity_executor=executor,
                    max_concurrent_activities=1,
                ),
            ):
                handle = await client.start_workflow(
                    ImageSupplementWorkflow.run,
                    run,
                    id=f"test-enhancement-supplement:{child_id}",
                    task_queue=settings.task_queue,
                )
                async with asyncio.timeout(30):
                    result = await handle.result()

        assert result["runtime_status"] == "completed"
        assert result["inspection_result"] == "normal"
        assert trace == ["enhance", "enhancement_review"]
        assert model_factory.calls == 1
        assert repo.load_step(child_id, "verify") is None
        assert repo.load_step(child_id, "detect") == observation.model_dump(mode="json")
        source = repo.load_step(child_id, "source")
        assert source["task"] == frozen_json
        assert repo.load_step(child_id, "enhancement_source")["decision"][
            "inspection_result"
        ] == "normal"
        assert repo.get_task(parent_id).model_dump(mode="json") == frozen_json
        assert repo.list_calls(parent_id) == ()
        assert [call.step_id for call in repo.list_calls(child_id)] == [
            "enhance",
            "enhancement_review",
        ]
        # A replayed admission must return the frozen child even when the local
        # deployment changes after completion. This touches only the dummy weight.
        checkpoint = settings.enhancement_models_dir / FixtureModel.model_version
        checkpoint.rename(checkpoint.with_suffix(".unavailable"))
        recovered = await starter.admit_supplement(
            parent_id,
            TaskSubmission(
                request_id=child.request_id,
                session_id=child.session_id,
                input_asset=child.input_asset,
                instruction=child.instruction,
            ),
            enhancement=EnhancementOptions(kind="dehaze"),
        )
        assert recovered.snapshot.task_id == child_id
        assert repo.get_run(child_id) == run
    finally:
        if handle and (await handle.describe()).status.name == "RUNNING":
            await handle.terminate("test cleanup")
