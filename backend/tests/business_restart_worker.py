"""Disposable real-workflow worker for the opt-in business restart acceptance."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from pipe_inspector.config import Settings
from pipe_inspector.contracts.detection import Detection, DetectionBatch
from pipe_inspector.contracts.inspection import ImageReview
from pipe_inspector.media import MediaStore
from pipe_inspector.storage.human_review import HumanReviewRepository
from pipe_inspector.workflow.activities import ImageActivities
from pipe_inspector.workflow.human_review_activities import HumanReviewActivities
from pipe_inspector.workflow.image import ImageInspectionWorkflow
from pipe_inspector.workflow.video import VideoInspectionWorkflow
from pipe_inspector.workflow.video_activities import VideoActivities
from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Worker

DETECTOR_ID = "business-restart-fixture"
MODEL_VERSION = "fixture-v1"
LABEL_SPACE_VERSION = "five"
VLM_MODEL = "business-restart-reviewer-v1"
_FRAME_NAME = re.compile(r"frame-(\d+)\.(?:png|bmp)$")


class CallTrace:
    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, kind: str, **values: object) -> None:
        payload = {
            "kind": kind,
            "pid": os.getpid(),
            "time_ns": time.time_ns(),
            **values,
        }
        encoded = (json.dumps(payload, sort_keys=True) + "\n").encode()
        descriptor = os.open(self.path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.write(descriptor, encoded)
        finally:
            os.close(descriptor)


class DeterministicDetector:
    detector_id = DETECTOR_ID
    model_version = MODEL_VERSION
    label_space_version = LABEL_SPACE_VERSION
    capabilities = SimpleNamespace(supports_batch=False)

    def __init__(self, trace: CallTrace) -> None:
        self.trace = trace

    def predict_image(self, path: Path) -> DetectionBatch:
        match = _FRAME_NAME.fullmatch(path.name)
        frame_index = int(match.group(1)) if match else None
        self.trace.append(
            "detect",
            input_kind="video_frame" if frame_index is not None else "image_original",
            frame_index=frame_index,
        )
        detections = ()
        if frame_index is None:
            detections = (
                Detection(
                    label="AJ",
                    native_label="AJ",
                    score=0.65,
                    bbox_xyxy=(8, 8, 40, 40),
                ),
            )
        return DetectionBatch(
            detector_id=self.detector_id,
            model_version=self.model_version,
            label_space_version=self.label_space_version,
            width=64,
            height=64,
            detections=detections,
        )


class DeterministicReviewer:
    def __init__(self, trace: CallTrace) -> None:
        self.trace = trace

    async def review(self, _path: Path, observation) -> ImageReview:
        count = len(observation.detection.detections)
        self.trace.append(
            "review",
            input_kind="image_original" if count else "video_frame",
            detection_count=count,
        )
        return ImageReview(
            image_assessable=True,
            full_image_audited=True,
            candidates=(
                ({"index": 0, "verdict": "abstain", "reason": "fixture ambiguity"},)
                if count
                else ()
            ),
            additional_anomalies=(),
            summary="deterministic business restart review",
        )


class FirstChunkGateRepository(HumanReviewRepository):
    def __init__(self, *args, gate: Path, trace: CallTrace, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.gate = gate
        self.trace = trace

    def commit_video_chunk(self, task_id, observations, *, attempt=1):
        state = super().commit_video_chunk(task_id, observations, attempt=attempt)
        if state.next_frame == 2 and not self.gate.exists():
            payload = {
                "pid": os.getpid(),
                "task_id": str(task_id),
                "next_frame": state.next_frame,
                "attempt": attempt,
            }
            with self.gate.open("x", encoding="utf-8") as output:
                json.dump(payload, output, sort_keys=True)
                output.write("\n")
            self.trace.append("first_chunk_committed", **payload)
            while True:
                activity.heartbeat(state.next_frame, state.last_timestamp_seconds or 0)
                time.sleep(0.2)
        return state


def _settings(args: argparse.Namespace) -> Settings:
    return Settings(
        asset_root=args.asset_root,
        task_queue=args.task_queue,
        vlm_base_url="https://fixture.invalid/v1",
        vlm_model=VLM_MODEL,
        vlm_api_key="fixture-only",
        api_timeout_seconds=5,
        image_review_mode="candidate_review",
    )


async def serve(args: argparse.Namespace) -> None:
    settings = _settings(args)
    trace = CallTrace(args.trace)
    repository = FirstChunkGateRepository(
        settings.database_dsn,
        schema=args.schema,
        gate=args.gate,
        trace=trace,
    )
    media = MediaStore(
        settings.asset_root,
        max_upload_bytes=settings.max_upload_bytes,
        max_pixels=settings.max_media_pixels,
        probe_timeout_seconds=settings.media_probe_timeout_seconds,
    )
    detector = DeterministicDetector(trace)
    reviewer = DeterministicReviewer(trace)
    images = ImageActivities(settings, repository, media, detector=detector, reviewer=reviewer)
    videos = VideoActivities(settings, repository, media, detector=detector, reviewer=reviewer)
    reviews = HumanReviewActivities(repository)
    client = await Client.connect(
        settings.temporal_address, namespace=settings.temporal_namespace
    )
    with (
        ThreadPoolExecutor(max_workers=4) as cpu_executor,
        ThreadPoolExecutor(max_workers=1) as gpu_executor,
    ):
        cpu = Worker(
            client,
            task_queue=settings.task_queue,
            workflows=[ImageInspectionWorkflow, VideoInspectionWorkflow],
            activities=[
                images.publish_stage,
                images.verify_image,
                images.finish_image,
                videos.prepare_video,
                videos.verify_video_frame,
                videos.finish_video,
                reviews.apply_human_review,
                reviews.cancel_human_review,
            ],
            activity_executor=cpu_executor,
            max_concurrent_activities=4,
        )
        gpu = Worker(
            client,
            task_queue=settings.task_queue + "-gpu",
            activities=[images.detect_image, videos.scan_video],
            activity_executor=gpu_executor,
            max_concurrent_activities=1,
        )
        async with cpu, gpu:
            trace.append("worker_ready")
            with args.ready.open("x", encoding="utf-8") as ready:
                json.dump({"pid": os.getpid()}, ready)
                ready.write("\n")
            await asyncio.Future()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-queue", required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--asset-root", type=Path, required=True)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--gate", type=Path, required=True)
    parser.add_argument("--ready", type=Path, required=True)
    args = parser.parse_args()
    for name in ("asset_root", "trace", "gate", "ready"):
        if not getattr(args, name).is_absolute():
            parser.error(f"--{name.replace('_', '-')} must be absolute")
    return args


if __name__ == "__main__":
    asyncio.run(serve(_parse_args()))
