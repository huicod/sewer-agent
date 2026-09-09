"""Lost completion and final-projection fault injection on real local Temporal/PG."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pipe_inspector.contracts.detection import Detection
from pipe_inspector.contracts.inspection import CandidateReview, ImageReview
from pipe_inspector.workflow.activities import ImageActivities
from pipe_inspector.workflow.image import ImageInspectionWorkflow
from pipe_inspector.workflow.start import run_config
from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Worker
from test_image_workflow import EmptyDetector, case  # noqa: F401

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("SEWER_RUN_IMAGE_WORKFLOW_TEST") != "1",
        reason="opt-in local Temporal/PG fault test",
    ),
]


class CandidateDetector(EmptyDetector):
    def predict_image(self, path):
        batch = super().predict_image(path)
        return batch.model_copy(
            update={
                "detections": (
                    Detection(label="FS", native_label="FS", score=0.6, bbox_xyxy=(4, 4, 40, 40)),
                )
            }
        )


async def test_committed_detection_and_final_checkpoint_recover_without_repeating_tools(case):  # noqa: F811
    settings, repo, media, task = case
    detector = CandidateDetector()
    detector.release.set()
    reviewer = SimpleNamespace(
        review=AsyncMock(
            return_value=ImageReview(
                image_assessable=True,
                full_image_audited=True,
                candidates=(
                    CandidateReview(
                        index=0, verdict="accept", proposed_label="FS", reason="同意候选"
                    ),
                ),
                additional_anomalies=(),
                summary="全图审核完成",
            )
        )
    )
    activities = ImageActivities(settings, repo, media, detector=detector, reviewer=reviewer)
    task_id = task.snapshot.task_id
    run = repo.ensure_run(task_id, run_config(settings, detector))
    detector_attempts, final_attempts, original_final = [], [], []
    update_snapshot = repo.update_snapshot

    def fail_first_terminal_projection(snapshot):
        if snapshot.is_terminal and not original_final:
            original_final.append(snapshot.model_dump(mode="json"))
            raise RuntimeError("injected final projection failure")
        return update_snapshot(snapshot)

    repo.update_snapshot = fail_first_terminal_projection

    @activity.defn(name="detect_image")
    def lose_first_completion(payload: dict) -> dict:
        detector_attempts.append(activity.info().attempt)
        result = activities.detect_image(payload)
        if activity.info().attempt == 1:
            raise RuntimeError("injected lost completion after database commit")
        return result

    @activity.defn(name="finish_image")
    def finalize(payload: dict) -> dict:
        final_attempts.append(activity.info().attempt)
        return activities.finish_image(payload)

    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    handle = None
    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            async with (
                Worker(
                    client,
                    task_queue=settings.task_queue,
                    workflows=[ImageInspectionWorkflow],
                    activities=[activities.publish_stage, activities.verify_image, finalize],
                    activity_executor=executor,
                ),
                Worker(
                    client,
                    task_queue=settings.task_queue + "-gpu",
                    activities=[lose_first_completion],
                    activity_executor=executor,
                    max_concurrent_activities=1,
                ),
            ):
                handle = await client.start_workflow(
                    ImageInspectionWorkflow.run,
                    run,
                    id=f"test-fault:{task_id}",
                    task_queue=settings.task_queue,
                )
                async with asyncio.timeout(40):
                    result = await handle.result()
        assert detector_attempts == [1, 2]
        assert final_attempts == [1, 2]
        assert detector.calls == 1
        reviewer.review.assert_awaited_once()
        assert result == original_final[0] == repo.load_step(task_id, "final")
        assert repo.get_task(task_id).snapshot.model_dump(mode="json") == result
        assert result["runtime_status"] == "completed"
        assert result["inspection_result"] == "defects_found"
        assert result["findings"][0]["canonical_label"] == "FS"
        assert len(repo.list_calls(task_id)) == 2
    finally:
        if handle and (await handle.describe()).status.name == "RUNNING":
            await handle.terminate("test cleanup")
