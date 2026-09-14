"""Opt-in real business workflow recovery across managed Temporal/PG restart."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import time
import warnings
from collections import Counter
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import psycopg
import pytest
from PIL import Image
from pipe_inspector.config import Settings
from pipe_inspector.contracts.human_review import FindingReview, HumanReviewSubmission
from pipe_inspector.contracts.inspection import ImageObservation
from pipe_inspector.contracts.intake import TaskSubmission
from pipe_inspector.media import MediaStore
from pipe_inspector.storage.human_review import HumanReviewRepository
from pipe_inspector.workflow.human_review_start import HumanReviewStarter
from pipe_inspector.workflow.start import InspectionStarter, run_config, video_run_config
from psycopg import sql
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.service import RPCError, RPCStatusCode
from test_recovery import assert_dedicated_cluster, assert_local_targets

from backend.tests.business_restart_support import (
    assert_app_stopped,
    assert_workflow_scope,
    load_restart_output,
)
from backend.tests.business_restart_worker import (
    DETECTOR_ID,
    LABEL_SPACE_VERSION,
    MODEL_VERSION,
    VLM_MODEL,
)

ROOT = Path(__file__).resolve().parents[3]
PYTHON = ROOT / ".venv" / "bin" / "python"
ENABLED = all(
    os.environ.get(name) == "1"
    for name in ("SEWER_RUN_RESTART_TEST", "SEWER_RUN_BUSINESS_RESTART_TEST")
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not ENABLED,
        reason="requires both explicit business and shared-service restart gates",
    ),
]


def _settings(output: Path, token: str) -> Settings:
    return Settings(
        asset_root=output / "assets",
        task_queue=f"business-restart-{token}",
        vlm_base_url="https://fixture.invalid/v1",
        vlm_model=VLM_MODEL,
        vlm_api_key="fixture-only",
        api_timeout_seconds=5,
        image_review_mode="candidate_review",
    )


def _json_command(arguments: list[str], *, label: str, timeout: float) -> dict:
    result = subprocess.run(
        arguments,
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"{label} failed with exit {result.returncode}")
    try:
        payload = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        raise RuntimeError(f"{label} returned invalid status") from None
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} returned invalid status")
    return payload


def _app_status() -> dict:
    return _json_command(
        [str(PYTHON), "scripts/app.py", "status"], label="app status", timeout=20
    )


def _runtime(command: str) -> dict:
    return _json_command(
        [str(PYTHON), "scripts/runtime.py", command],
        label=f"runtime {command}",
        timeout=75,
    )


def _restart_postgres() -> None:
    result = subprocess.run(
        [
            "/usr/bin/pg_ctlcluster",
            "--skip-systemctl-redirect",
            "14",
            "main",
            "restart",
        ],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=45,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"PostgreSQL 14/main restart failed with exit {result.returncode}")


def _write_summary(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _create_schema(settings: Settings, schema: str) -> None:
    with psycopg.connect(settings.database_dsn.get_secret_value()) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))


def _drop_schema(settings: Settings, schema: str) -> None:
    with psycopg.connect(settings.database_dsn.get_secret_value()) as connection:
        connection.execute(
            sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
        )


def _postgres_start_time(settings: Settings) -> str:
    with psycopg.connect(settings.database_dsn.get_secret_value()) as connection:
        started_at = connection.execute("SELECT pg_postmaster_start_time()").fetchone()[0]
    return started_at.isoformat()


def _make_fixtures(output: Path) -> tuple[Path, Path]:
    fixtures = output / "fixtures"
    fixtures.mkdir()
    image_path = fixtures / "image.png"
    image = Image.new("RGB", (64, 64))
    image.putdata(
        [
            ((255 if (x // 4 + y // 4) % 2 else 0), (x * 4) % 256, (y * 4) % 256)
            for y in range(64)
            for x in range(64)
        ]
    )
    image.save(image_path)
    video_path = fixtures / "six-frames.mp4"
    result = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=64x64:r=5",
            "-frames:v",
            "6",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            str(video_path),
        ],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"synthetic video creation failed with exit {result.returncode}")
    return image_path, video_path


def _ingest_task(repo, media, path: Path, session_id: UUID, instruction: str):
    with path.open("rb") as source:
        asset = media.ingest(source)
    repo.save_asset(asset)
    return repo.create_task(
        TaskSubmission(
            request_id=uuid4(),
            session_id=session_id,
            input_asset=asset.ref,
            instruction=instruction,
        )
    )


def _start_worker(
    settings: Settings,
    schema: str,
    output: Path,
    *,
    ordinal: int,
) -> subprocess.Popen:
    ready = output / f"worker-{ordinal}.ready.json"
    environment = os.environ.copy()
    environment["TMPDIR"] = str(output / "tmp")
    log_path = output / f"worker-{ordinal}.log"
    with log_path.open("ab") as log:
        return subprocess.Popen(
            [
                str(PYTHON),
                "-m",
                "backend.tests.business_restart_worker",
                "--task-queue",
                settings.task_queue,
                "--schema",
                schema,
                "--asset-root",
                str(settings.asset_root.resolve()),
                "--trace",
                str((output / "call-trace.jsonl").resolve()),
                "--gate",
                str((output / "first-chunk.json").resolve()),
                "--ready",
                str(ready.resolve()),
            ],
            cwd=ROOT,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )


async def _await_worker_ready(worker: subprocess.Popen, ready: Path) -> None:
    async with asyncio.timeout(30):
        while True:
            if worker.poll() is not None:
                raise RuntimeError(f"test worker exited with {worker.returncode}")
            try:
                payload = json.loads(ready.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError):
                await asyncio.sleep(0.05)
                continue
            if payload == {"pid": worker.pid}:
                return
            raise RuntimeError("test worker readiness identity changed")


def _stop_worker_group(worker: subprocess.Popen | None, *, hard: bool) -> None:
    if worker is None or worker.poll() is not None:
        return
    os.killpg(worker.pid, signal.SIGKILL if hard else signal.SIGTERM)
    try:
        worker.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(worker.pid, signal.SIGKILL)
        worker.wait(timeout=10)


async def _running_workflows(client: Client) -> list[dict[str, str]]:
    return [
        {"workflow_id": item.id, "run_id": item.run_id}
        async for item in client.list_workflows(
            'ExecutionStatus = "Running"', rpc_timeout=timedelta(seconds=10)
        )
    ]


async def _wait_for_owned_workflows(
    client: Client, owned_ids: set[str]
) -> list[dict[str, str]]:
    async with asyncio.timeout(30):
        while True:
            running = await _running_workflows(client)
            ids = {item["workflow_id"] for item in running}
            if ids - owned_ids:
                assert_workflow_scope(list(ids), owned_ids)
            if ids == owned_ids:
                return running
            await asyncio.sleep(0.1)


def _chunk_rows(repo: HumanReviewRepository, task_id: UUID) -> list[tuple[int, int, int]]:
    with repo._connect() as connection:
        return connection.execute(
            sql.SQL(
                "SELECT start_frame, end_frame, attempt FROM {} "
                "WHERE task_id = %s ORDER BY start_frame"
            ).format(repo._table("sewer_video_chunks")),
            (task_id,),
        ).fetchall()


def _trace(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _trace_summary(events: list[dict]) -> dict:
    calls = [event for event in events if event["kind"] in {"detect", "review"}]
    return {
        "counts": dict(
            Counter(f"{item['kind']}:{item['input_kind']}" for item in calls)
        ),
        "video_detection_frames": [
            item["frame_index"]
            for item in calls
            if item["kind"] == "detect" and item["input_kind"] == "video_frame"
        ],
        "worker_pids": sorted({item["pid"] for item in events}),
    }


async def _terminate_if_running(client: Client | None, workflow_id: str) -> None:
    if client is None:
        return
    handle = client.get_workflow_handle(workflow_id)
    try:
        description = await handle.describe(rpc_timeout=timedelta(seconds=5))
    except RPCError as error:
        if error.status == RPCStatusCode.NOT_FOUND:
            return
        raise
    if description.status == WorkflowExecutionStatus.RUNNING:
        await handle.terminate(
            reason="business restart test cleanup",
            rpc_timeout=timedelta(seconds=5),
        )


async def test_business_workflows_survive_temporal_and_postgres_restart() -> None:
    output = load_restart_output(os.environ, repo_root=ROOT)
    settings = _settings(output, uuid4().hex)
    assert_local_targets(settings)
    assert_dedicated_cluster()
    assert_app_stopped(_app_status())
    client = await Client.connect(
        settings.temporal_address, namespace=settings.temporal_namespace
    )
    initial_running = await _running_workflows(client)
    assert_workflow_scope([item["workflow_id"] for item in initial_running], set())

    output.mkdir(parents=True, exist_ok=False)
    (output / "tmp").mkdir()
    summary_path = output / "business-restart-summary.json"
    trace_path = output / "call-trace.jsonl"
    gate_path = output / "first-chunk.json"
    token = uuid4().hex
    schema = f"business_restart_{token}"
    evidence: dict = {
        "kind": "business_service_restart_acceptance",
        "status": "running",
        "phase": "setup",
        "schema": schema,
        "substitutes": {
            "detector": DETECTOR_ID,
            "detector_model": MODEL_VERSION,
            "reviewer_model": VLM_MODEL,
            "external_api_calls": 0,
        },
        "pins": {
            "detector_id": DETECTOR_ID,
            "model_version": MODEL_VERSION,
            "label_space_version": LABEL_SPACE_VERSION,
            "reviewer_model": VLM_MODEL,
            "image_review_mode": "candidate_review",
            "video_chunk_size": 2,
            "synthetic_video_frames": 6,
        },
        "processes": [],
        "workflows": [],
    }
    _write_summary(summary_path, evidence)

    schema_created = False
    runtime_interrupted = False
    worker: subprocess.Popen | None = None
    workflow_ids: set[str] = set()
    client_for_cleanup: Client | None = client
    failure: BaseException | None = None
    cleanup_errors: list[str] = []
    try:
        _create_schema(settings, schema)
        schema_created = True
        repo = HumanReviewRepository(settings.database_dsn, schema=schema)
        repo.initialize()
        media = MediaStore(settings.asset_root)
        image_path, video_path = _make_fixtures(output)
        session_id = repo.create_session().session_id
        image_task = _ingest_task(repo, media, image_path, session_id, "审核合成管道图片")
        video_task = _ingest_task(repo, media, video_path, session_id, "检查合成六帧视频")
        pins = SimpleNamespace(
            detector_id=DETECTOR_ID,
            model_version=MODEL_VERSION,
            label_space_version=LABEL_SPACE_VERSION,
        )
        image_run = repo.ensure_run(
            image_task.snapshot.task_id,
            run_config(settings, pins, force_review=True),
        )
        video_config = video_run_config(settings, pins)
        video_config["video_chunk_size"] = 2
        video_run = repo.ensure_run(video_task.snapshot.task_id, video_config)
        image_id = f"inspection:{image_task.snapshot.task_id}"
        video_id = f"inspection:{video_task.snapshot.task_id}"
        workflow_ids = {image_id, video_id}
        evidence["business_run_ids"] = {
            "image": image_run["run_id"],
            "video": video_run["run_id"],
        }

        evidence["phase"] = "first_worker"
        worker = _start_worker(settings, schema, output, ordinal=1)
        evidence["processes"].append({"ordinal": 1, "pid": worker.pid})
        _write_summary(summary_path, evidence)
        await _await_worker_ready(worker, output / "worker-1.ready.json")
        starter = InspectionStarter(settings, repo)
        await starter.start(image_task.snapshot.task_id, force_review=True)
        image_handle = client.get_workflow_handle(image_id)
        async with asyncio.timeout(30):
            while await image_handle.query("current_stage") != "human_review":
                await asyncio.sleep(0.1)
        await starter.start(video_task.snapshot.task_id)
        video_handle = client.get_workflow_handle(video_id)

        async with asyncio.timeout(90):
            while True:
                image_stage = await image_handle.query("current_stage")
                video_snapshot = repo.get_task(video_task.snapshot.task_id).snapshot
                if (
                    image_stage == "human_review"
                    and video_snapshot.processed_frames == 2
                    and video_snapshot.processed_chunks == 1
                    and gate_path.exists()
                ):
                    break
                await asyncio.sleep(0.1)

        gate = json.loads(gate_path.read_text(encoding="utf-8"))
        assert gate["pid"] == worker.pid
        assert gate["task_id"] == str(video_task.snapshot.task_id)
        assert gate["next_frame"] == 2
        before_image = repo.get_task(image_task.snapshot.task_id).snapshot
        before_detect = repo.load_step(image_task.snapshot.task_id, "detect")
        before_verify = repo.load_step(image_task.snapshot.task_id, "verify")
        before_final = repo.load_step(image_task.snapshot.task_id, "final")
        before_calls = [
            call.model_dump(mode="json") for call in repo.list_calls(image_task.snapshot.task_id)
        ]
        observation = ImageObservation.model_validate(before_detect)
        assert observation.detection.detections[0].bbox_xyxy == (8, 8, 40, 40)
        assert before_image.runtime_status == "waiting_review"
        assert before_image.findings and before_verify is not None and before_final is not None
        before_chunks = _chunk_rows(repo, video_task.snapshot.task_id)
        before_video_calls = [
            call.model_dump(mode="json") for call in repo.list_calls(video_task.snapshot.task_id)
        ]
        assert [(start, end) for start, end, _ in before_chunks] == [(0, 1)]
        before_trace = _trace(trace_path)
        before_video = [
            item["frame_index"]
            for item in before_trace
            if item["kind"] == "detect" and item["input_kind"] == "video_frame"
        ]
        assert before_video == [0, 1]
        assert sum(
            item["kind"] == "detect" and item["input_kind"] == "image_original"
            for item in before_trace
        ) == 1
        assert sum(
            item["kind"] == "review" and item["input_kind"] == "image_original"
            for item in before_trace
        ) == 1

        running = await _wait_for_owned_workflows(client, workflow_ids)
        assert_app_stopped(_app_status())
        assert_workflow_scope([item["workflow_id"] for item in running], workflow_ids)
        evidence["workflows"] = running
        evidence["before_restart"] = {
            "image_snapshot": before_image.model_dump(mode="json"),
            "image_detect": before_detect,
            "image_verify": before_verify,
            "image_tool_calls": before_calls,
            "video_snapshot": video_snapshot.model_dump(mode="json"),
            "video_chunks": before_chunks,
            "video_tool_calls": before_video_calls,
            "first_chunk_gate": gate,
            "trace": _trace_summary(before_trace),
        }
        evidence["phase"] = "restart"
        _write_summary(summary_path, evidence)

        assert_app_stopped(_app_status())
        current_running = await _running_workflows(client)
        assert_workflow_scope(
            [item["workflow_id"] for item in current_running], workflow_ids
        )
        before_workflow_runs = {
            item["workflow_id"]: item["run_id"] for item in current_running
        }
        runtime_before = _runtime("status")
        service_before = {
            "temporal_pid": runtime_before["temporal"]["pid"],
            "postgres_start_time": _postgres_start_time(settings),
        }
        assert isinstance(service_before["temporal_pid"], int)
        evidence["service_before_restart"] = service_before
        evidence["workflow_runs_before_restart"] = before_workflow_runs
        _write_summary(summary_path, evidence)
        first_worker_pid = worker.pid
        _stop_worker_group(worker, hard=True)
        worker = None
        restart_started = time.perf_counter()
        runtime_interrupted = True
        _runtime("stop")
        _restart_postgres()
        runtime_after = _runtime("start")
        service_restart_seconds = time.perf_counter() - restart_started
        runtime_interrupted = False
        recovery_started = time.perf_counter()
        service_after = {
            "temporal_pid": runtime_after["temporal"]["pid"],
            "postgres_start_time": _postgres_start_time(settings),
        }
        assert isinstance(service_after["temporal_pid"], int)
        assert service_after["temporal_pid"] != service_before["temporal_pid"]
        assert service_after["postgres_start_time"] != service_before["postgres_start_time"]

        client = await Client.connect(
            settings.temporal_address, namespace=settings.temporal_namespace
        )
        client_for_cleanup = client
        after_restart_workflows = await _wait_for_owned_workflows(client, workflow_ids)
        after_workflow_runs = {
            item["workflow_id"]: item["run_id"] for item in after_restart_workflows
        }
        assert after_workflow_runs == before_workflow_runs
        evidence["service_after_restart"] = service_after
        evidence["workflow_runs_after_restart"] = after_workflow_runs
        _write_summary(summary_path, evidence)
        image_handle = client.get_workflow_handle(image_id)
        video_handle = client.get_workflow_handle(video_id)
        assert (await image_handle.describe()).status == WorkflowExecutionStatus.RUNNING
        assert (await video_handle.describe()).status == WorkflowExecutionStatus.RUNNING
        worker = _start_worker(settings, schema, output, ordinal=2)
        second_worker_pid = worker.pid
        evidence["processes"].append({"ordinal": 2, "pid": second_worker_pid})
        await _await_worker_ready(worker, output / "worker-2.ready.json")

        async with asyncio.timeout(180):
            while await image_handle.query("current_stage") != "human_review":
                await asyncio.sleep(0.1)
            video_result = await video_handle.result()
        recovery_seconds = time.perf_counter() - recovery_started
        assert recovery_seconds < 180
        after_wait_image = repo.get_task(image_task.snapshot.task_id).snapshot
        assert after_wait_image == before_image
        assert repo.load_step(image_task.snapshot.task_id, "detect") == before_detect
        assert repo.load_step(image_task.snapshot.task_id, "verify") == before_verify
        assert repo.load_step(image_task.snapshot.task_id, "final") == before_final
        after_image_calls = [
            call.model_dump(mode="json") for call in repo.list_calls(image_task.snapshot.task_id)
        ]
        assert after_image_calls == before_calls

        video_state = repo.load_video_state(video_task.snapshot.task_id)
        video_snapshot = repo.get_task(video_task.snapshot.task_id).snapshot
        after_chunks = _chunk_rows(repo, video_task.snapshot.task_id)
        assert video_result["runtime_status"] == "completed"
        assert video_state is not None and video_state.next_frame == 6
        assert video_snapshot.processed_frames == 6
        assert video_snapshot.processed_chunks == 3
        assert [(start, end) for start, end, _ in after_chunks] == [(0, 1), (2, 3), (4, 5)]
        assert [
            repo.get_video_frame(video_task.snapshot.task_id, index).frame_index
            for index in range(6)
        ] == list(range(6))

        after_trace = _trace(trace_path)
        video_detects = [
            item
            for item in after_trace
            if item["kind"] == "detect" and item["input_kind"] == "video_frame"
        ]
        assert Counter(item["frame_index"] for item in video_detects) == Counter(range(6))
        assert {item["pid"] for item in video_detects[:2]} == {first_worker_pid}
        assert {item["pid"] for item in video_detects[2:]} == {second_worker_pid}
        assert sum(
            item["kind"] == "detect" and item["input_kind"] == "image_original"
            for item in after_trace
        ) == 1
        assert sum(
            item["kind"] == "review" and item["input_kind"] == "image_original"
            for item in after_trace
        ) == 1

        submission = HumanReviewSubmission(
            request_id=uuid4(),
            reviewer="business-restart-fixture",
            snapshot_updated_at=before_image.updated_at,
            decisions=tuple(
                FindingReview(
                    finding_id=finding.finding_id,
                    action="confirm",
                    reason="synthetic evidence audited",
                )
                for finding in before_image.findings
            ),
            negative_audit_complete=True,
            note="synthetic image only",
        )
        review = await HumanReviewStarter(settings, repo).submit(
            image_task.snapshot.task_id, submission
        )
        image_result = await image_handle.result()
        assert review.before == before_image
        assert review.after.runtime_status == "completed"
        assert review.after.findings[0].confirmed_by == "human"
        assert image_result["runtime_status"] == "completed"

        async def unavailable(*_args, **_kwargs):
            raise AssertionError("idempotent review must not reconnect to Temporal")

        replayed = await HumanReviewStarter(settings, repo, connect=unavailable).submit(
            image_task.snapshot.task_id, submission
        )
        assert replayed == review
        assert repo.get_human_review(image_task.snapshot.task_id) == review

        evidence.update(
            status="passed",
            phase="validated",
            timings={
                "service_restart_seconds": service_restart_seconds,
                "recovery_after_temporal_ready_seconds": recovery_seconds,
            },
            after_restart={
                "image_snapshot_before_review": after_wait_image.model_dump(mode="json"),
                "image_tool_calls": after_image_calls,
                "image_review": review.model_dump(mode="json"),
                "video_snapshot": video_snapshot.model_dump(mode="json"),
                "video_state": video_state.model_dump(mode="json"),
                "video_chunks": after_chunks,
                "video_tool_calls": [
                    call.model_dump(mode="json")
                    for call in repo.list_calls(video_task.snapshot.task_id)
                ],
                "trace": _trace_summary(after_trace),
            },
        )
        _write_summary(summary_path, evidence)
    except BaseException as error:
        failure = error
        evidence.update(
            status="failed",
            failure={"phase": evidence.get("phase"), "type": type(error).__name__},
        )
        _write_summary(summary_path, evidence)
    finally:
        if runtime_interrupted:
            try:
                _runtime("start")
                runtime_interrupted = False
            except (OSError, RuntimeError, subprocess.SubprocessError):
                cleanup_errors.append("managed runtime recovery failed")
        try:
            _stop_worker_group(worker, hard=False)
        except (OSError, subprocess.SubprocessError):
            cleanup_errors.append("test worker cleanup failed")
        for workflow_id in workflow_ids:
            try:
                await _terminate_if_running(client_for_cleanup, workflow_id)
            except (OSError, RuntimeError, RPCError):
                cleanup_errors.append(f"workflow cleanup failed: {workflow_id}")
        if schema_created:
            try:
                _drop_schema(settings, schema)
            except (OSError, psycopg.Error):
                cleanup_errors.append("test schema cleanup failed")
        evidence["cleanup"] = {
            "runtime_recovered": not runtime_interrupted,
            "errors": cleanup_errors,
        }
        if cleanup_errors:
            evidence["status"] = "failed"
            evidence.setdefault(
                "failure", {"phase": "cleanup", "type": "CleanupError"}
            )
        _write_summary(summary_path, evidence)

    if failure is not None:
        if cleanup_errors:
            warnings.warn("; ".join(cleanup_errors), stacklevel=1)
        raise failure
    if cleanup_errors:
        pytest.fail("; ".join(cleanup_errors))
