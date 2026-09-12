#!/usr/bin/env python3
"""Run one CPU/API worker or one serial GPU worker on this host."""

import argparse
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from pipe_inspector.config import Settings
from pipe_inspector.media import MediaStore
from pipe_inspector.storage.human_review import HumanReviewRepository
from pipe_inspector.workflow.activities import ImageActivities
from pipe_inspector.workflow.conversation import ConversationWorkflow
from pipe_inspector.workflow.conversation_activities import TurnActivities
from pipe_inspector.workflow.enhancement_activities import EnhancementActivities
from pipe_inspector.workflow.enhancement_selection import EnhancementSelectionActivities
from pipe_inspector.workflow.human_review_activities import HumanReviewActivities
from pipe_inspector.workflow.image import ImageInspectionWorkflow
from pipe_inspector.workflow.retired_open_scan import RetiredOpenScanActivities
from pipe_inspector.workflow.supplement import ImageSupplementWorkflow
from pipe_inspector.workflow.supplement_activities import SupplementActivities
from pipe_inspector.workflow.video import VideoInspectionWorkflow
from pipe_inspector.workflow.video_activities import VideoActivities
from temporalio.client import Client
from temporalio.worker import Worker


async def serve(kind: str) -> None:
    settings = Settings()
    repo = HumanReviewRepository(settings.database_dsn)
    media = MediaStore(
        settings.asset_root,
        max_upload_bytes=settings.max_upload_bytes,
        max_pixels=settings.max_media_pixels,
        probe_timeout_seconds=settings.media_probe_timeout_seconds,
    )
    activities = ImageActivities(settings, repo, media)
    enhancements = EnhancementActivities(settings, repo, media)
    enhancement_selection = EnhancementSelectionActivities(settings, repo, media)
    videos = VideoActivities(settings, repo, media, detector=activities.detector)
    # Same activity names only drain old histories; no open model is loaded or run.
    open_scans = RetiredOpenScanActivities(repo)
    supplements = SupplementActivities(settings, repo, media)
    turns = TurnActivities(settings, repo)
    human_reviews = HumanReviewActivities(repo)
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    gpu = kind == "gpu"
    # Temporal logs Activity arguments on failure. Our DB tool records contain safe codes;
    # disable this particular SDK logger to avoid duplicating private metadata in logs.
    logging.getLogger("temporalio.activity").setLevel(logging.CRITICAL)
    with ThreadPoolExecutor(max_workers=1 if gpu else 4) as executor:
        worker = Worker(
            client,
            task_queue=settings.task_queue + ("-gpu" if gpu else ""),
            workflows=[]
            if gpu
            else [
                ImageInspectionWorkflow,
                VideoInspectionWorkflow,
                ConversationWorkflow,
                ImageSupplementWorkflow,
            ],
            activities=[
                activities.detect_image,
                videos.scan_video,
                open_scans.scan_open_candidate,
                enhancements.enhance_image,
            ]
            if gpu
            else [
                activities.publish_stage,
                activities.verify_image,
                activities.finish_image,
                enhancements.prepare_image_enhancement,
                enhancements.review_image_enhancement,
                enhancement_selection.select_image_enhancement,
                open_scans.select_open_scan,
                videos.prepare_video,
                videos.verify_video_frame,
                videos.finish_video,
                supplements.finish_supplement,
                turns.parse_turn,
                turns.dispatch_turn,
                turns.finish_turn,
                human_reviews.apply_human_review,
                human_reviews.cancel_human_review,
            ],
            activity_executor=executor,
            max_concurrent_activities=1 if gpu else 4,
            graceful_shutdown_timeout=timedelta(seconds=10),
        )
        print(f"inspection {kind} worker ready; queue={worker.task_queue}", flush=True)
        await worker.run()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("cpu", "gpu"))
    args = parser.parse_args()
    try:
        asyncio.run(serve(args.kind))
    except KeyboardInterrupt:
        pass
