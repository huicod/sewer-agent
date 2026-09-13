#!/usr/bin/env python3
"""Run one CPU/API worker or one serial GPU worker on this host."""

import argparse
import asyncio
import json
import logging
import os
import signal
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

from pipe_inspector.config import Settings
from pipe_inspector.media import MediaStore
from pipe_inspector.storage.human_review import HumanReviewRepository
from pipe_inspector.workflow.activities import ImageActivities
from pipe_inspector.workflow.conversation import ConversationWorkflow
from pipe_inspector.workflow.conversation_activities import TurnActivities
from pipe_inspector.workflow.enhancement_activities import EnhancementActivities
from pipe_inspector.workflow.enhancement_selection import EnhancementSelectionActivities
from pipe_inspector.workflow.geometry_activities import GeometryActivities
from pipe_inspector.workflow.human_review_activities import HumanReviewActivities
from pipe_inspector.workflow.image import ImageInspectionWorkflow
from pipe_inspector.workflow.retired_open_scan import RetiredOpenScanActivities
from pipe_inspector.workflow.supplement import ImageSupplementWorkflow
from pipe_inspector.workflow.supplement_activities import SupplementActivities
from pipe_inspector.workflow.video import VideoInspectionWorkflow
from pipe_inspector.workflow.video_activities import VideoActivities
from temporalio.client import Client
from temporalio.worker import Worker


async def run_worker(worker: Worker, kind: str, ready_file: Path | None = None) -> None:
    """Publish process readiness after startup, then drain on local stop signals."""
    loop = asyncio.get_running_loop()
    stopped = asyncio.Event()
    signals = (signal.SIGTERM, signal.SIGINT)
    previous = {sig: signal.getsignal(sig) for sig in signals}
    for sig in signals:
        loop.add_signal_handler(sig, stopped.set)
    running = asyncio.create_task(worker.run())
    stopping = asyncio.create_task(stopped.wait())
    published = False
    try:
        while not running.done() and not stopped.is_set():
            if worker.is_running:
                if ready_file is not None:
                    with ready_file.open("x", encoding="utf-8") as output:
                        published = True
                        os.chmod(ready_file, 0o600)
                        json.dump({"pid": os.getpid(), "kind": kind}, output)
                print(f"inspection {kind} worker ready", flush=True)
                break
            await asyncio.sleep(0.01)
        await asyncio.wait((running, stopping), return_when=asyncio.FIRST_COMPLETED)
        if not running.done():
            await worker.shutdown()
        await running
    finally:
        if published:
            ready_file.unlink(missing_ok=True)
        stopping.cancel()
        await asyncio.gather(stopping, return_exceptions=True)
        try:
            if not running.done():
                await worker.shutdown()
                await running
        finally:
            for sig in signals:
                loop.remove_signal_handler(sig)
                signal.signal(sig, previous[sig])


async def serve(kind: str, ready_file: Path | None = None) -> None:
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
    geometry = GeometryActivities(settings, repo, media)
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
                geometry.segment_image,
                geometry.estimate_relative_depth,
            ]
            if gpu
            else [
                activities.publish_stage,
                activities.verify_image,
                activities.finish_image,
                enhancements.prepare_image_enhancement,
                enhancements.review_image_enhancement,
                geometry.prepare_image_geometry,
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
        await run_worker(worker, kind, ready_file)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("cpu", "gpu"))
    parser.add_argument("--ready-file", type=Path)
    args = parser.parse_args()
    if args.ready_file is not None and not args.ready_file.is_absolute():
        parser.error("--ready-file must be absolute")
    try:
        asyncio.run(serve(args.kind, args.ready_file))
    except KeyboardInterrupt:
        pass
