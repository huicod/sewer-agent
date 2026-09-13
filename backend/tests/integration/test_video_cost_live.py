"""Opt-in full production video workflow resource and provider-usage acceptance."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from pipe_inspector.config import Settings
from pipe_inspector.contracts.assets import AssetRef
from pipe_inspector.contracts.intake import TaskSubmission
from pipe_inspector.media import MediaStore
from pipe_inspector.storage.human_review import HumanReviewRepository
from pipe_inspector.workflow.start import video_run_config
from pipe_inspector.workflow.video import VideoInspectionWorkflow
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from temporalio.client import Client
from temporalio.service import RPCError, RPCStatusCode
from temporalio.worker import Worker

TESTS_ROOT = Path(__file__).resolve().parents[1]
if str(TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TESTS_ROOT))

from browser_live import build_live_components  # noqa: E402
from video_cost_live import (  # noqa: E402
    ActivityExecutionRecorder,
    MeasuredVideoActivities,
    VideoCostAdmission,
    load_video_cost_admission,
    require_bounded_api_output,
    summarize_measurements,
)

pytestmark = [pytest.mark.integration, pytest.mark.gpu, pytest.mark.live_api]
_OBSERVATION_TIMEOUT_SECONDS = 3600


def _live_settings(output: Path, token: str) -> Settings:
    base = Settings()
    temporal_host = base.temporal_address.rsplit(":", 1)[0].strip("[]")
    database_host = conninfo_to_dict(base.database_dsn.get_secret_value()).get("host", "")
    local_hosts = {"", "127.0.0.1", "localhost", "::1"}
    if temporal_host not in local_hosts or database_host not in local_hosts:
        raise RuntimeError("video cost acceptance requires local PostgreSQL and Temporal")
    values = base.model_dump()
    values.update(
        asset_root=output / "assets",
        task_queue=f"video-cost-{token}",
        open_detector_path=None,
        open_text_encoder_path=None,
    )
    settings = require_bounded_api_output(Settings(_env_file=None, **values))
    if not settings.vlm_configured:
        raise RuntimeError("video cost acceptance requires the configured production VLM")
    return settings


def _create_schema(settings: Settings, schema: str) -> None:
    with psycopg.connect(settings.database_dsn.get_secret_value()) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))


def _drop_schema(settings: Settings, schema: str) -> None:
    with psycopg.connect(settings.database_dsn.get_secret_value()) as connection:
        connection.execute(
            sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
        )


def _copy_source(admission: VideoCostAdmission) -> tuple[Path, dict[str, object]]:
    fixtures = admission.output / "fixtures"
    fixtures.mkdir()
    suffix = admission.source.suffix.casefold() or ".mp4"
    target = fixtures / f"video{suffix}"
    shutil.copyfile(admission.source, target)
    return target, {
        "artifact": str(target.relative_to(admission.output)),
        "byte_size": target.stat().st_size,
        "duration_seconds": admission.duration_seconds,
        "width": admission.width,
        "height": admission.height,
    }


async def _observe_processing(handle, repository, task_id: UUID, components) -> str:
    last_reported: tuple[str, int, int] | None = None
    next_periodic = 0.0
    async with asyncio.timeout(_OBSERVATION_TIMEOUT_SECONDS):
        while True:
            stage = await handle.query("current_stage")
            state = await asyncio.to_thread(repository.load_video_state, task_id)
            processed_frames = 0 if state is None else state.next_frame
            physical_calls = len(components.recorder.snapshot()["provider_calls"])
            progress = (stage, processed_frames, physical_calls)
            now = time.monotonic()
            if progress != last_reported and (stage != last_reported[0] if last_reported else True):
                print(
                    "video-cost progress: "
                    f"stage={stage} processed_frames={processed_frames} "
                    f"physical_api_requests={physical_calls}",
                    flush=True,
                )
                last_reported = progress
                next_periodic = now + 5
            elif now >= next_periodic:
                print(
                    "video-cost progress: "
                    f"stage={stage} processed_frames={processed_frames} "
                    f"physical_api_requests={physical_calls}",
                    flush=True,
                )
                last_reported = progress
                next_periodic = now + 5

            final = await asyncio.to_thread(repository.load_step, task_id, "final")
            if final is not None and stage == "human_review":
                return stage
            description = await handle.describe()
            if description.status.name != "RUNNING":
                return stage
            await asyncio.sleep(0.5)


def _write_evidence(
    admission: VideoCostAdmission,
    repository: HumanReviewRepository,
    media: MediaStore,
    task_id: UUID,
    run: dict,
    components,
    timing: ActivityExecutionRecorder,
    *,
    source: dict[str, object],
    started_at: datetime,
    observed_at: datetime,
    processing_elapsed_seconds: float,
    observed_stage: str | None,
    error_type: str | None,
) -> None:
    scan = repository.load_step(task_id, "video_scan")
    evidence = repository.load_step(task_id, "video_evidence") or {
        "frames": [],
        "review_frames": [],
    }
    final = repository.load_step(task_id, "final")
    task = repository.get_task(task_id)
    tool_calls = [item.model_dump(mode="json") for item in repository.list_calls(task_id)]
    with repository._connect() as connection:
        chunk_count = connection.execute(
            sql.SQL("SELECT count(*) FROM {} WHERE task_id = %s").format(
                repository._table("sewer_video_chunks")
            ),
            (task_id,),
        ).fetchone()[0]

    review_frames = set(evidence["review_frames"])
    frame_dir = admission.output / "evidence-frames"
    frame_records = []
    if evidence["frames"]:
        frame_dir.mkdir(exist_ok=True)
    for item in evidence["frames"]:
        index = int(item["frame_index"])
        ref = AssetRef.model_validate(item["asset"])
        destination = frame_dir / f"frame-{index:06d}.png"
        shutil.copyfile(media.path_for(ref), destination)
        observation = repository.get_video_frame(task_id, index)
        frame_records.append(
            {
                **item,
                "artifact": str(destination.relative_to(admission.output)),
                "selected_for_review": index in review_frames,
                "observation": observation.model_dump(mode="json"),
                "review_checkpoint": repository.load_step(
                    task_id, f"video_verify:{index}"
                ),
            }
        )

    provider = components.recorder.snapshot()
    gpu = components.gpu.snapshot()
    activity_snapshot = timing.snapshot()
    measurements = summarize_measurements(
        provider=provider,
        activity=activity_snapshot,
        processing_elapsed_seconds=processing_elapsed_seconds,
    )
    scan_state = scan.get("state", {}) if scan else {}
    events = scan_state.get("events", [])
    payload = {
        "kind": "live_video_cost_acceptance",
        "started_at": started_at.isoformat(),
        "processing_observed_at": observed_at.isoformat(),
        "observed_stage": observed_stage,
        "human_review_observed": observed_stage == "human_review",
        "harness_error_type": error_type,
        "source": source,
        "coverage": {
            "processed_frames": scan_state.get("next_frame", 0),
            "chunk_count": chunk_count,
            "decode_complete": None if scan is None else scan.get("decode_complete"),
            "decode_error_code": None if scan is None else scan.get("error_code"),
            "event_count": len(events),
            "events": events,
            "evidence_frame_count": len(frame_records),
            "selected_review_frame_count": len(evidence["review_frames"]),
            "selected_review_frames": evidence["review_frames"],
            "detector_batch_count": sum(
                item["operation"] == "detect_batch" for item in gpu["serial_calls"]
            ),
            "detector_image_call_count": sum(
                item["operation"] == "detect_image" for item in gpu["serial_calls"]
            ),
        },
        **measurements,
        "activity_attempt_log": activity_snapshot["calls"],
        "provider_calls": provider["provider_calls"],
        "provider_failed_calls": provider["failed_calls"],
        "provider_usage_unknown_calls": provider["usage_unknown_calls"],
        "gpu": gpu,
        "run": run,
        "final_task": task.model_dump(mode="json"),
        "final_checkpoint": final,
        "tool_calls": tool_calls,
        "evidence_frames": frame_records,
        "billing_amount": None,
        "billing_note": (
            "Provider-returned usage is measured separately from any currency estimate or bill."
        ),
    }
    (admission.output / "video-cost-summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


async def _terminate_if_running(handle) -> None:
    if handle is None:
        return
    try:
        description = await handle.describe()
    except RPCError as error:
        if error.status == RPCStatusCode.NOT_FOUND:
            return
        raise
    if description.status.name == "RUNNING":
        await handle.terminate(
            "video cost acceptance captured human gate; no human approval fabricated"
        )


async def test_live_video_cost_acceptance() -> None:
    admission = load_video_cost_admission(os.environ)
    if admission is None:
        pytest.skip("requires both explicit video cost and live API gates")

    admission.output.mkdir(parents=True, exist_ok=False)
    fixture, source_metadata = _copy_source(admission)
    token = uuid4().hex
    settings = _live_settings(admission.output, token)
    schema = "video_cost_" + token
    schema_created = False
    handle = None
    try:
        _create_schema(settings, schema)
        schema_created = True
        repository = HumanReviewRepository(settings.database_dsn, schema=schema)
        repository.initialize()
        media = MediaStore(
            settings.asset_root,
            max_upload_bytes=settings.max_upload_bytes,
            max_pixels=settings.max_media_pixels,
            probe_timeout_seconds=settings.media_probe_timeout_seconds,
        )
        with fixture.open("rb") as stream:
            asset = media.ingest(stream)
        repository.save_asset(asset)
        session = repository.create_session()
        task = repository.create_task(
            TaskSubmission(
                request_id=uuid4(),
                session_id=session.session_id,
                input_asset=asset.ref,
                instruction="检查完整视频并保留所有当前生产复检与人工审核策略",
            )
        )
        components = build_live_components(settings, repository, media)
        timing = ActivityExecutionRecorder()
        videos = MeasuredVideoActivities(
            settings,
            repository,
            media,
            detector=components.detector,
            reviewer=components.reviewer,
            timing=timing,
        )
        config = video_run_config(settings, components.detector)
        assert config["video_review_budget"] == 32
        assert config["human_review_version"] == "human-review-v1"
        run = repository.ensure_run(task.snapshot.task_id, config)
        client = await Client.connect(
            settings.temporal_address, namespace=settings.temporal_namespace
        )
        observed_stage = None
        caught: BaseException | None = None
        with (
            ThreadPoolExecutor(max_workers=4) as cpu_executor,
            ThreadPoolExecutor(max_workers=1) as gpu_executor,
        ):
            async with (
                Worker(
                    client,
                    task_queue=settings.task_queue,
                    workflows=[VideoInspectionWorkflow],
                    activities=[
                        videos.prepare_video,
                        videos.verify_video_frame,
                        videos.finish_video,
                    ],
                    activity_executor=cpu_executor,
                    max_concurrent_activities=4,
                ),
                Worker(
                    client,
                    task_queue=settings.task_queue + "-gpu",
                    activities=[videos.scan_video],
                    activity_executor=gpu_executor,
                    max_concurrent_activities=1,
                ),
            ):
                try:
                    started_at = datetime.now(timezone.utc)
                    processing_started = time.perf_counter()
                    handle = await client.start_workflow(
                        VideoInspectionWorkflow.run,
                        run,
                        id=f"video-cost:{token}:{task.snapshot.task_id}",
                        task_queue=settings.task_queue,
                    )
                    observed_stage = await _observe_processing(
                        handle, repository, task.snapshot.task_id, components
                    )
                    observed_at = datetime.now(timezone.utc)
                    processing_elapsed_seconds = time.perf_counter() - processing_started
                except BaseException as error:
                    caught = error
                    observed_at = datetime.now(timezone.utc)
                    processing_elapsed_seconds = time.perf_counter() - processing_started
                finally:
                    _write_evidence(
                        admission,
                        repository,
                        media,
                        task.snapshot.task_id,
                        run,
                        components,
                        timing,
                        source=source_metadata,
                        started_at=started_at,
                        observed_at=observed_at,
                        processing_elapsed_seconds=processing_elapsed_seconds,
                        observed_stage=observed_stage,
                        error_type=None if caught is None else type(caught).__name__,
                    )
                    await _terminate_if_running(handle)
                if caught is not None:
                    raise caught

        scan = repository.load_step(task.snapshot.task_id, "video_scan")
        final = repository.load_step(task.snapshot.task_id, "final")
        provider = components.recorder.snapshot()
        assert scan is not None and scan["decode_complete"] is True
        assert scan["state"]["next_frame"] > 0
        assert final is not None
        assert observed_stage == "human_review" or final["runtime_status"] in {
            "completed",
            "partial",
            "failed",
            "cancelled",
        }
        assert len(provider["provider_calls"]) <= 8
    finally:
        await _terminate_if_running(handle)
        if schema_created:
            _drop_schema(settings, schema)
