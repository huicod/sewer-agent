"""Real PG/Temporal review recovery with deterministic, local model substitutes."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pipe_inspector.contracts.detection import Detection, DetectionBatch
from pipe_inspector.contracts.human_review import FindingReview, HumanReviewSubmission
from pipe_inspector.contracts.inspection import ImageReview
from pipe_inspector.storage.human_review import HumanReviewRepository
from pipe_inspector.workflow.activities import ImageActivities
from pipe_inspector.workflow.human_review_activities import HumanReviewActivities
from pipe_inspector.workflow.human_review_start import HumanReviewStarter
from pipe_inspector.workflow.image import ImageInspectionWorkflow
from pipe_inspector.workflow.start import InspectionStarter, run_config
from temporalio.client import Client
from temporalio.worker import Worker
from test_image_workflow import case as image_case  # noqa: F401

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("SEWER_RUN_HUMAN_REVIEW_WORKFLOW_TEST") != "1",
        reason="opt-in local PostgreSQL/Temporal review recovery test",
    ),
]


@pytest.fixture
def review_case(request):
    settings, old_repo, media, task = request.getfixturevalue("image_case")
    repo = HumanReviewRepository(settings.database_dsn, schema=old_repo._schema)
    repo.initialize()
    batch = DetectionBatch(
        detector_id="fixture",
        model_version="v1",
        label_space_version="five",
        width=64,
        height=64,
        detections=(
            Detection(label="AJ", native_label="AJ", score=0.65, bbox_xyxy=(10, 10, 40, 40)),
        ),
    )
    detector = SimpleNamespace(
        detector_id="fixture",
        model_version="v1",
        label_space_version="five",
        predict_image=Mock(return_value=batch),
    )
    reviewer = SimpleNamespace(
        review=AsyncMock(
            return_value=ImageReview(
                image_assessable=True,
                full_image_audited=True,
                candidates=({"index": 0, "verdict": "abstain", "reason": "fixture ambiguity"},),
                additional_anomalies=(),
                summary="等待人工判定",
            )
        )
    )
    images = ImageActivities(settings, repo, media, detector=detector, reviewer=reviewer)
    reviews = HumanReviewActivities(repo)
    run = repo.ensure_run(task.snapshot.task_id, run_config(settings, detector))
    return settings, repo, images, reviews, run, detector, reviewer


async def _await_review(handle):
    async with asyncio.timeout(20):
        while await handle.query("current_stage") != "human_review":
            await asyncio.sleep(0.05)


@pytest.mark.parametrize("cancel", [False, True])
async def test_waiting_review_survives_worker_replacement_without_repeating_models(
    review_case, cancel
):
    settings, repo, images, reviews, run, detector, reviewer = review_case
    from uuid import UUID

    task_id = UUID(run["task_id"])
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    handle = None
    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            activity_list = [
                images.publish_stage,
                images.verify_image,
                images.finish_image,
                reviews.apply_human_review,
                reviews.cancel_human_review,
            ]
            async with Worker(
                client,
                task_queue=settings.task_queue + "-gpu",
                activities=[images.detect_image],
                activity_executor=executor,
            ):
                async with Worker(
                    client,
                    task_queue=settings.task_queue,
                    workflows=[ImageInspectionWorkflow],
                    activities=activity_list,
                    activity_executor=executor,
                ):
                    handle = await client.start_workflow(
                        ImageInspectionWorkflow.run,
                        run,
                        id=f"inspection:{task_id}",
                        task_queue=settings.task_queue,
                    )
                    await _await_review(handle)
                    before = repo.get_task(task_id).snapshot
                    assert before.runtime_status == "waiting_review"
                    assert before.findings and before.active_tool is None
            # Both workers are stopped. Task and evidence remain queryable from the DB.
            reopened = HumanReviewRepository(settings.database_dsn, schema=repo._schema)
            assert reopened.get_task(task_id).snapshot == before
            assert (await handle.describe()).status.name == "RUNNING"
            # Only CPU worker returns; replay must not request another detector call.
            async with Worker(
                client,
                task_queue=settings.task_queue,
                workflows=[ImageInspectionWorkflow],
                activities=activity_list,
                activity_executor=executor,
            ):
                await _await_review(handle)
                if cancel:
                    await InspectionStarter(settings, reopened).cancel(task_id)
                else:
                    body = HumanReviewSubmission(
                        request_id=uuid4(),
                        reviewer="huicod",
                        snapshot_updated_at=before.updated_at,
                        decisions=tuple(
                            FindingReview(
                                finding_id=f.finding_id, action="confirm", reason="逐项核对证据"
                            )
                            for f in before.findings
                        ),
                    )
                    # Emulate a DB commit whose Activity completion acknowledgement was lost.
                    apply = repo.apply_human_review
                    committed = []

                    def lose_first_ack(*args):
                        record = apply(*args)
                        committed.append(record)
                        if len(committed) == 1:
                            raise RuntimeError("fixture acknowledgement loss after durable commit")
                        return record

                    repo.apply_human_review = lose_first_ack
                    result = await HumanReviewStarter(settings, reopened).submit(task_id, body)
                    assert result.before == before
                    assert result.after.inspection_result == "defects_found"
                    assert result.after.findings[0].confirmed_by == "human"
                    assert len(committed) == 2 and committed[0] == committed[1]
                async with asyncio.timeout(20):
                    receipt = await handle.result()
            assert receipt["runtime_status"] == ("cancelled" if cancel else "completed")
            detector.predict_image.assert_called_once()
            reviewer.review.assert_awaited_once()
            if not cancel:
                unavailable = AsyncMock(side_effect=AssertionError("must use persisted audit"))
                retry = await HumanReviewStarter(settings, reopened, connect=unavailable).submit(
                    task_id, body
                )
                assert retry == result
                unavailable.assert_not_awaited()
                assert reopened.load_step(task_id, "final")["runtime_status"] == "waiting_review"
                assert reopened.get_task(task_id).snapshot == result.after
    finally:
        if handle and (await handle.describe()).status.name == "RUNNING":
            await handle.terminate("isolated test cleanup")
