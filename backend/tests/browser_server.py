#!/usr/bin/env python3
"""Isolated real-stack HTTP service for browser acceptance testing."""

import asyncio
import contextlib
import json
import logging
import signal
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import psycopg
import uvicorn
from browser_fixtures import (
    BrowserDetector,
    BrowserInspectionStarter,
    BrowserIntentParser,
    BrowserReviewer,
    create_media_fixtures,
    prepare_output,
)
from pipe_inspector.agent.conversation import ConversationDispatcher
from pipe_inspector.api.app import create_app
from pipe_inspector.config import Settings
from pipe_inspector.media import MediaStore
from pipe_inspector.storage.human_review import HumanReviewRepository
from pipe_inspector.workflow.activities import ImageActivities
from pipe_inspector.workflow.conversation import ConversationWorkflow
from pipe_inspector.workflow.conversation_activities import TurnActivities
from pipe_inspector.workflow.human_review_activities import HumanReviewActivities
from pipe_inspector.workflow.image import ImageInspectionWorkflow
from pipe_inspector.workflow.video import VideoInspectionWorkflow
from pipe_inspector.workflow.video_activities import VideoActivities
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from pydantic import SecretStr
from temporalio.client import Client
from temporalio.service import RPCError, RPCStatusCode
from temporalio.worker import Worker

HOST = "127.0.0.1"
PORT = 18080


class _ManagedServer(uvicorn.Server):
    """Let this harness finish owned-resource cleanup before process exit."""

    @contextlib.contextmanager
    def capture_signals(self):
        yield


def _settings(output: Path, token: str) -> Settings:
    base = Settings()
    temporal_host = base.temporal_address.rsplit(":", 1)[0].strip("[]")
    database_host = conninfo_to_dict(base.database_dsn.get_secret_value()).get("host", "")
    local_hosts = {"", "127.0.0.1", "localhost", "::1"}
    if temporal_host not in local_hosts or database_host not in local_hosts:
        raise RuntimeError("browser acceptance requires local PostgreSQL and Temporal")
    return Settings(
        _env_file=None,
        database_dsn=base.database_dsn,
        temporal_address=base.temporal_address,
        temporal_namespace=base.temporal_namespace,
        asset_root=output / "assets",
        task_queue=f"browser-acceptance-{token}",
        detector_path=Path("browser-fixture-no-checkpoint"),
        detector_device="cpu",
        llm_base_url="https://fixture.invalid/v1",
        llm_model="browser-fixture-intent-v1",
        llm_api_key=SecretStr("browser-fixture-only"),
        vlm_base_url="https://fixture.invalid/v1",
        vlm_model="browser-fixture-reviewer-v1",
        vlm_api_key=SecretStr("browser-fixture-only"),
        api_timeout_seconds=5,
        api_enable_thinking=False,
        api_max_output_tokens=512,
        max_upload_bytes=8 * 1024**2,
        max_media_pixels=100_000,
        media_probe_timeout_seconds=10,
        vlm_max_image_bytes=1024**2,
        image_review_mode="candidate_review",
        open_detector_path=None,
        open_text_encoder_path=None,
    )


def _create_schema(settings: Settings, schema: str) -> None:
    with psycopg.connect(settings.database_dsn.get_secret_value()) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))


def _owned_workflow_ids(repository) -> tuple[str, ...]:
    with repository._connect() as connection:
        task_rows = connection.execute(
            sql.SQL("SELECT task_id FROM {}").format(repository._table("sewer_tasks"))
        ).fetchall()
        turn_rows = connection.execute(
            sql.SQL("SELECT request_id FROM {}").format(
                repository._table("sewer_conversation_turns")
            )
        ).fetchall()
    return tuple(
        [f"inspection:{row[0]}" for row in task_rows]
        + [f"conversation:{row[0]}" for row in turn_rows]
    )


async def _terminate_owned(client: Client, repository) -> int:
    terminated = 0
    for workflow_id in _owned_workflow_ids(repository):
        handle = client.get_workflow_handle(workflow_id)
        try:
            description = await handle.describe()
        except RPCError as error:
            if error.status == RPCStatusCode.NOT_FOUND:
                continue
            raise
        if description.status.name == "RUNNING":
            await handle.terminate("browser acceptance service shutdown")
            terminated += 1
    return terminated


def _write_snapshot(
    output: Path,
    repository,
    detector: BrowserDetector,
    reviewer: BrowserReviewer,
    parser: BrowserIntentParser,
    *,
    started_at: datetime,
    terminated_workflows: int,
) -> None:
    with repository._connect() as connection:
        tasks = [
            row[0]
            for row in connection.execute(
                sql.SQL("SELECT record FROM {} ORDER BY created_at, task_id").format(
                    repository._table("sewer_tasks")
                )
            ).fetchall()
        ]
        turns = [
            {"record": row[0], "checkpoints": row[1]}
            for row in connection.execute(
                sql.SQL(
                    "SELECT record, checkpoints FROM {} ORDER BY created_at, request_id"
                ).format(repository._table("sewer_conversation_turns"))
            ).fetchall()
        ]
        reviews = [
            {"before": row[0], "after": row[1]}
            for row in connection.execute(
                sql.SQL(
                    "SELECT before_snapshot, after_snapshot FROM {} ORDER BY reviewed_at"
                ).format(repository._table("sewer_human_reviews"))
            ).fetchall()
        ]
    ended_at = datetime.now(timezone.utc)
    payload = {
        "kind": "synthetic_browser_acceptance",
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "elapsed_seconds": (ended_at - started_at).total_seconds(),
        "detector_calls": detector.calls,
        "reviewer_calls": reviewer.calls,
        "intent_calls": parser.calls,
        "terminated_workflows": terminated_workflows,
        "tasks": tasks,
        "turns": turns,
        "human_reviews": reviews,
    }
    (output / "service-summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


async def _serve(
    settings: Settings,
    repository: HumanReviewRepository,
    output: Path,
    started_at: datetime,
) -> None:
    client = await Client.connect(
        settings.temporal_address, namespace=settings.temporal_namespace
    )
    media = MediaStore(
        settings.asset_root,
        max_upload_bytes=settings.max_upload_bytes,
        max_pixels=settings.max_media_pixels,
        probe_timeout_seconds=settings.media_probe_timeout_seconds,
    )
    detector = BrowserDetector()
    reviewer = BrowserReviewer()
    parser = BrowserIntentParser()
    starter = BrowserInspectionStarter(settings, repository, detector)
    turns = TurnActivities(
        settings,
        repository,
        parser=parser,
        dispatcher=ConversationDispatcher(repository, starter),
    )
    images = ImageActivities(
        settings, repository, media, detector=detector, reviewer=reviewer
    )
    videos = VideoActivities(
        settings, repository, media, detector=detector, reviewer=reviewer
    )
    reviews = HumanReviewActivities(repository)
    app = create_app(settings, repository=repository, starter=starter)
    server = _ManagedServer(
        uvicorn.Config(app, host=HOST, port=PORT, log_level="info", access_log=True)
    )
    loop = asyncio.get_running_loop()

    def request_exit() -> None:
        server.should_exit = True

    for handled_signal in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(handled_signal, request_exit)
    terminated = 0
    logging.getLogger("temporalio.activity").setLevel(logging.CRITICAL)
    try:
        with (
            ThreadPoolExecutor(max_workers=4) as cpu_executor,
            ThreadPoolExecutor(max_workers=1) as gpu_executor,
        ):
            async with (
                Worker(
                    client,
                    task_queue=settings.task_queue,
                    workflows=[
                        ConversationWorkflow,
                        ImageInspectionWorkflow,
                        VideoInspectionWorkflow,
                    ],
                    activities=[
                        turns.parse_turn,
                        turns.dispatch_turn,
                        turns.finish_turn,
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
                ),
                Worker(
                    client,
                    task_queue=settings.task_queue + "-gpu",
                    activities=[images.detect_image, videos.scan_video],
                    activity_executor=gpu_executor,
                    max_concurrent_activities=1,
                ),
            ):
                print(
                    f"browser acceptance workers ready; http://{HOST}:{PORT}/health",
                    flush=True,
                )
                await server.serve()
                if not server.started:
                    raise RuntimeError("browser acceptance HTTP server failed to start")
    finally:
        # Both workers have stopped: no in-flight dispatch can create a task
        # after the owned-workflow snapshot and leave it on a dead test queue.
        try:
            async with asyncio.timeout(15):
                terminated = await _terminate_owned(client, repository)
        finally:
            for handled_signal in (signal.SIGINT, signal.SIGTERM):
                loop.remove_signal_handler(handled_signal)
            _write_snapshot(
                output,
                repository,
                detector,
                reviewer,
                parser,
                started_at=started_at,
                terminated_workflows=terminated,
            )


async def main() -> None:
    started_at = datetime.now(timezone.utc)
    output = prepare_output()
    create_media_fixtures(output)
    token = uuid4().hex
    settings = _settings(output, token)
    schema = "browser_acceptance_" + token
    created = False
    try:
        _create_schema(settings, schema)
        created = True
        repository = HumanReviewRepository(settings.database_dsn, schema=schema)
        repository.initialize()
        await _serve(settings, repository, output, started_at)
    finally:
        if created:
            with psycopg.connect(
                settings.database_dsn.get_secret_value()
            ) as connection:
                connection.execute(
                    sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
                )


if __name__ == "__main__":
    asyncio.run(main())
