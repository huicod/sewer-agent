"""Selected-frame dialogue and existing tools on real PG/Temporal, local model doubles."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import numpy as np
import pytest
from pipe_inspector.agent.conversation import ConversationDispatcher
from pipe_inspector.contracts.assets import AssetRef
from pipe_inspector.contracts.conversation import ConversationIntent
from pipe_inspector.contracts.turns import TurnSubmission
from pipe_inspector.workflow import geometry_config
from pipe_inspector.workflow.enhancement_activities import EnhancementActivities
from pipe_inspector.workflow.geometry_activities import GeometryActivities
from pipe_inspector.workflow.start import InspectionStarter
from pipe_inspector.workflow.supplement import ImageSupplementWorkflow
from pipe_inspector.workflow.supplement_activities import SupplementActivities
from temporalio import activity
from temporalio.client import Client
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment
from temporalio.worker import Worker

from backend.tests.integration.test_enhancement_workflow_live import (
    FixtureModel,
    FixtureModelFactory,
    FixtureReviewer,
)
from backend.tests.integration.test_geometry_workflow_live import Depth, Segmenter
from backend.tests.integration.test_video_workflow import case as case

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("SEWER_RUN_FRAME_TOOL_TEST") != "1",
        reason="opt-in local PG/Temporal selected-video-frame tool test",
    ),
]


class FrameSegmenter(Segmenter):
    def segment_image(self, image, prompt):
        from pipe_inspector.vision.geometry import MaskPrediction

        self.trace.append("segment")
        values = np.zeros((image.height, image.width), dtype=bool)
        values[2:4, 3:6] = True
        return MaskPrediction(values, 0.8)


class FrameDepth(Depth):
    def predict_depth(self, image):
        self.trace.append("depth")
        return np.tile(np.arange(image.width, dtype=np.float32), (image.height, 1))


@pytest.mark.parametrize("operation", ["enhance", "relative_measure"])
async def test_selected_frame_dialogue_recovers_tool_ack_without_rerunning_video(
    case, tmp_path, monkeypatch, operation
):
    settings, repo, media, detector, reviewer, videos, parent_run = case
    parent_id = UUID(parent_run["task_id"])
    env = ActivityEnvironment()
    await asyncio.to_thread(env.run, videos.scan_video, parent_run)
    selected = await asyncio.to_thread(env.run, videos.prepare_video, parent_run)
    for index in selected["review_frames"]:
        await env.run(videos.verify_video_frame, {"run": parent_run, "frame_index": index})
    await asyncio.to_thread(env.run, videos.finish_video, {"run": parent_run})
    parent = repo.get_task(parent_id)
    assert parent.snapshot.runtime_status == "completed"
    frozen_parent = parent.model_dump(mode="json")
    frozen_calls = repo.list_calls(parent_id)
    original_counts = (detector.calls, reviewer.review.await_count)
    evidence = repo.load_step(parent_id, "video_evidence")
    frame = next(
        item for item in evidence["frames"]
        if item["frame_index"] == selected["review_frames"][0]
    )
    frame_ref = AssetRef.model_validate(frame["asset"])
    settings.enhancement_models_dir = tmp_path / "models"
    settings.enhancement_models_dir.mkdir()
    (settings.enhancement_models_dir / FixtureModel.model_version).write_bytes(b"fixture")
    # Model doubles need no downloaded geometry files; production pin metadata is unchanged.
    build_geometry = geometry_config.build_geometry_config
    monkeypatch.setattr(
        geometry_config, "build_geometry_config",
        lambda cfg, options, check_files=True: build_geometry(cfg, options, check_files=False),
    )
    intent = ConversationIntent(
        kind="supplement", actions=(operation,), message="显式所选帧工具",
        **({"enhancement": {"kind": "dehaze"}} if operation == "enhance" else {
            "geometry": {"operation": "relative_measure", "roi_xyxy": [2, 1, 7, 5]},
        }),
    )
    submission = TurnSubmission(
        request_id=uuid4(), session_id=parent.session_id, task_id=parent_id,
        input_asset=frame_ref, text="对所选视频证据帧执行显式工具",
    )
    turn = repo.admit_turn(submission, {})
    trace = []
    enhancements = EnhancementActivities(
        settings, repo, media, model_factory=FixtureModelFactory(trace),
        reviewer=FixtureReviewer(trace),
    )
    geometry = GeometryActivities(
        settings, repo, media,
        segmenter_factory=lambda checkpoint, device: FrameSegmenter(trace),
        depth_factory=lambda checkpoint, device: FrameDepth(trace),
    )
    finalizer = SupplementActivities(settings, repo, media, detector=detector, reviewer=reviewer)
    lost_ack = False

    @activity.defn(name="enhance_image")
    def enhance_losing_ack(run):
        nonlocal lost_ack
        result = enhancements.enhance_image(run)
        if not lost_ack:
            lost_ack = True
            raise ApplicationError("fixture ack lost", type="fixture_ack_lost")
        return result

    @activity.defn(name="segment_image")
    def segment_losing_ack(run):
        nonlocal lost_ack
        result = geometry.segment_image(run)
        if not lost_ack:
            lost_ack = True
            raise ApplicationError("fixture ack lost", type="fixture_ack_lost")
        return result

    handle = None
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    dispatcher = ConversationDispatcher(repo, InspectionStarter(settings, repo))
    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            async with (
                Worker(
                    client, task_queue=settings.task_queue,
                    workflows=[ImageSupplementWorkflow],
                    activities=[
                        enhancements.prepare_image_enhancement,
                        enhancements.review_image_enhancement,
                        geometry.prepare_image_geometry,
                        finalizer.finish_supplement,
                    ],
                    activity_executor=executor,
                ),
                Worker(
                    client, task_queue=settings.task_queue + "-gpu",
                    activities=[enhance_losing_ack, segment_losing_ack,
                                geometry.estimate_relative_depth],
                    activity_executor=executor,
                ),
            ):
                reply = await dispatcher.handle(turn, intent)
                child_id = UUID(reply["linked_task_id"])
                handle = client.get_workflow_handle(f"inspection:{child_id}")
                result = await asyncio.wait_for(handle.result(), timeout=30)

        assert result["runtime_status"] == "completed"
        assert result["inspection_result"] == "normal"
        child = repo.get_task(child_id)
        assert child.input_asset == frame_ref and child.parent_task_id == parent_id
        assert child.frame_source.frame_index == frame["frame_index"]
        assert child.frame_source.timestamp_seconds == frame["timestamp_seconds"]
        assert child.frame_source.video_asset == parent.input_asset
        assert repo.load_step(child_id, "verify") is None
        assert repo.load_step(child_id, "frame_source")["decision"]["inspection_result"] == "normal"
        assert lost_ack
        assert trace == (["enhance", "enhancement_review"] if operation == "enhance" else [
            "segment", "unload_segment", "depth", "unload_depth",
        ])
        assert all(call.tool_name not in {"detect"} for call in repo.list_calls(child_id))
        assert repo.get_task(parent_id).model_dump(mode="json") == frozen_parent
        assert repo.list_calls(parent_id) == frozen_calls
        assert (detector.calls, reviewer.review.await_count) == original_counts
        # Same turn reuses completed child without requiring another worker/model execution.
        repeated = await dispatcher.handle(turn, intent)
        assert repeated["linked_task_id"] == str(child_id)
        assert "已复用" in repeated["reply"]
        assert len(repo.get_session(parent.session_id).tasks) == 2
    finally:
        if handle is not None and (await handle.describe()).status.name == "RUNNING":
            await handle.terminate("test-owned frame workflow cleanup")
