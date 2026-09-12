"""Opt-in real PG/Temporal recovery for an immutable automatic tool choice."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pipe_inspector.contracts.enhancement import EnhancementOptions, EnhancementProposal
from pipe_inspector.storage.repository import SubmissionConflict
from pipe_inspector.workflow.activities import ImageActivities
from pipe_inspector.workflow.enhancement_activities import EnhancementActivities
from pipe_inspector.workflow.enhancement_config import build_enhancement_config
from pipe_inspector.workflow.enhancement_selection import EnhancementSelectionActivities
from pipe_inspector.workflow.image import ImageInspectionWorkflow
from pipe_inspector.workflow.start import run_config
from temporalio import activity
from temporalio.client import Client
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment
from temporalio.worker import Worker
from test_enhancement_workflow_live import (
    FixtureDetector,
    FixtureModelFactory,
    FixtureReviewer,
)
from test_enhancement_workflow_live import live_case as live_case

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("SEWER_RUN_ENHANCEMENT_WORKFLOW_TEST") != "1",
        reason="opt-in local PG/Temporal automatic enhancement tests",
    ),
]


def setup_tools(live_case, *, skip=False):
    settings, repo, media, task = live_case
    trace = []
    detector, reviewer, factory = (
        FixtureDetector(trace),
        FixtureReviewer(trace),
        FixtureModelFactory(trace),
    )
    config = run_config(settings, detector)
    pin = build_enhancement_config(settings, EnhancementOptions(kind="dehaze"), check_files=False)
    config["auto_enhancement"] = {
        "version": "enhancement-selection-v1",
        "call_id": str(uuid4()),
        "models": {"dehaze": pin | {"options": None}},
    }
    config.pop("human_review_version")  # test terminal projection without waiting for a person
    run = repo.ensure_run(task.snapshot.task_id, config)

    async def propose(*args, **kwargs):
        trace.append("select")
        return EnhancementProposal(
            options=None if skip else EnhancementOptions(kind="dehaze"),
            degradation="none" if skip else "haze",
            confidence="clear",
            evidence="fixture haze evidence",
            inspection_impact="fixture impaired wall boundary",
        )

    original = ImageActivities(settings, repo, media, detector=detector, reviewer=reviewer)
    selection = EnhancementSelectionActivities(
        settings, repo, media, selector=SimpleNamespace(propose=propose)
    )
    enhancement = EnhancementActivities(
        settings, repo, media, reviewer=reviewer, model_factory=factory
    )
    return run, original, selection, enhancement, trace


@pytest.mark.asyncio
async def test_pg_requires_saved_choice_and_reuses_derived_asset_after_restart(live_case):
    settings, repo, media, task = live_case
    run, original, selector, enhancement, trace = setup_tools(live_case)
    env = ActivityEnvironment()
    env.run(original.detect_image, run)
    await env.run(original.verify_image, run)
    selected = await env.run(selector.select_image_enhancement, run)
    effective = run | {"config": run["config"] | {"enhancement": selected["enhancement"]}}
    env.run(enhancement.prepare_image_enhancement, effective)
    saved = env.run(enhancement.enhance_image, effective)
    assert env.run(enhancement.enhance_image, effective) == saved
    assert repo.get_run(task.snapshot.task_id)["config"] == run["config"]
    assert trace == ["detect", "verify", "select", "enhance"]
    assert "enhancement" not in repo.get_run(task.snapshot.task_id)["config"]

    # A different call cannot claim the selected tool's already committed result.
    from pipe_inspector.contracts.enhancement import EnhancementResult
    from pipe_inspector.contracts.tools import ToolCall

    call = next(c for c in repo.list_calls(task.snapshot.task_id) if c.step_id == "enhance")
    with pytest.raises(SubmissionConflict):
        repo.save_enhancement(
            task.snapshot.task_id,
            EnhancementResult.model_validate(saved),
            ToolCall.model_validate(call.model_dump() | {"call_id": uuid4()}),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("skip", [False, True])
async def test_temporal_selection_ack_loss_replays_choice_with_replaced_worker(live_case, skip):
    settings, repo, media, task = live_case
    run, original, selector, enhancement, trace = setup_tools(live_case, skip=skip)
    committed = asyncio.Event()

    @activity.defn(name="select_image_enhancement")
    async def lose_ack(run):
        await selector.select_image_enhancement(run)
        committed.set()
        raise ApplicationError("fixture lost selection acknowledgement", type="fixture_ack_lost")

    client = await Client.connect(settings.temporal_address)
    handle = None
    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            cpu_activities = [
                original.publish_stage,
                original.verify_image,
                enhancement.prepare_image_enhancement,
                enhancement.review_image_enhancement,
                original.finish_image,
            ]
            async with (
                Worker(
                    client,
                    task_queue=run["config"]["gpu_queue"],
                    activity_executor=executor,
                    activities=[original.detect_image, enhancement.enhance_image],
                    max_concurrent_activities=1,
                ),
                Worker(
                    client,
                    task_queue=settings.task_queue,
                    workflows=[ImageInspectionWorkflow],
                    activity_executor=executor,
                    activities=[*cpu_activities, lose_ack],
                ),
            ):
                handle = await client.start_workflow(
                    ImageInspectionWorkflow.run,
                    run,
                    id=f"test-adaptive:{task.snapshot.task_id}",
                    task_queue=settings.task_queue,
                )
                await asyncio.wait_for(committed.wait(), 20)
            selector.selector = None  # persisted choice, no second real/fake API client
            async with (
                Worker(
                    client,
                    task_queue=run["config"]["gpu_queue"],
                    activity_executor=executor,
                    activities=[original.detect_image, enhancement.enhance_image],
                    max_concurrent_activities=1,
                ),
                Worker(
                    client,
                    task_queue=settings.task_queue,
                    workflows=[ImageInspectionWorkflow],
                    activity_executor=executor,
                    activities=[*cpu_activities, selector.select_image_enhancement],
                ),
            ):
                async with asyncio.timeout(30):
                    result = await handle.result()
        assert result["runtime_status"] == "completed"
        assert trace == [
            "detect",
            "verify",
            "select",
            *([] if skip else ["enhance", "enhancement_review"]),
        ]
        selection = repo.load_step(task.snapshot.task_id, "enhancement_selection")["selection"]
        assert selection["status"] == ("skipped" if skip else "selected")
    finally:
        if handle and (await handle.describe()).status.name == "RUNNING":
            await handle.terminate("isolated adaptive test cleanup")
