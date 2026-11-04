#!/usr/bin/env python3
"""Container lifecycle only; native app.py services and data are never managed here."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.request
from datetime import timedelta
from pathlib import Path

import psycopg
from pipe_inspector.config import Settings
from pipe_inspector.contracts.geometry import GeometryOptions
from pipe_inspector.storage.human_review import HumanReviewRepository
from pipe_inspector.workflow.geometry_config import build_geometry_config
from psycopg.conninfo import make_conninfo

ROOT = Path(__file__).resolve().parents[2]
WORKER = ROOT / "scripts" / "inspection_worker.py"
STARTUP_TIMEOUT = 120.0
PROBE_TIMEOUT = 3.0
REQUIRED_TABLES = (
    "sewer_sessions",
    "sewer_assets",
    "sewer_tasks",
    "sewer_inspection_runs",
    "sewer_tool_calls",
    "sewer_step_checkpoints",
    "sewer_conversation_turns",
    "sewer_image_operations",
    "sewer_video_states",
    "sewer_video_chunks",
    "sewer_human_reviews",
    "sewer_video_frame_tools",
)


class ContainerError(RuntimeError):
    """An error containing only a safe, actionable description."""


def configure_environment() -> Settings:
    """Construct a libpq DSN so quotes, spaces and URI characters stay literal."""
    password = os.environ.get("SEWER_DB_PASSWORD")
    if not password:
        raise ContainerError("SEWER_DB_PASSWORD is required; run the Docker setup helper")
    try:
        # Compose interpolates unset optional values as empty strings. Treat an empty
        # per-role key as absent so Settings can apply its official Qwen shared-key rule.
        for key in (
            "SEWER_LLM_API_KEY",
            "SEWER_VLM_API_KEY",
            "DASHSCOPE_API_KEY",
            "SEWER_LLM_BASE_URL",
            "SEWER_VLM_BASE_URL",
            "SEWER_LLM_MODEL",
            "SEWER_VLM_MODEL",
        ):
            if os.environ.get(key) == "":
                os.environ.pop(key)
        port = int(os.environ.get("SEWER_DB_PORT", "5432"))
        if not 1 <= port <= 65535:
            raise ValueError
        os.environ["SEWER_DATABASE_DSN"] = make_conninfo(
            host=os.environ.get("SEWER_DB_HOST", "postgres"),
            port=str(port),
            dbname=os.environ.get("SEWER_DB_NAME", "sewer_agent"),
            user=os.environ.get("SEWER_DB_USER", "sewer_agent"),
            password=password,
            connect_timeout=str(int(PROBE_TIMEOUT)),
        )
        model_root = Path(os.environ.get("SEWER_MODEL_ROOT", "/models"))
        defaults = {
            "SEWER_TEMPORAL_ADDRESS": "temporal:7233",
            "SEWER_ASSET_ROOT": str(ROOT / "var" / "assets"),
            "SEWER_WEB_DIST": str(ROOT / "frontend" / "dist"),
            "SEWER_DETECTOR_PATH": str(model_root / "detector" / "best.pt"),
            "SEWER_ENHANCEMENT_MODELS_DIR": str(model_root / "enhancement"),
            "SEWER_GEOMETRY_MODELS_DIR": str(model_root / "geometry"),
        }
        for key, value in defaults.items():
            os.environ.setdefault(key, value)
        # Never consume an accidentally mounted native .env in a container.
        return Settings(_env_file=None)
    except Exception:
        raise ContainerError("container configuration is invalid; check .env.docker") from None


def _model_errors(model_root: Path) -> list[str]:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from scripts.model_bundle import check_bundle

    return check_bundle(model_root)


def validate_configuration(settings: Settings, role: str) -> None:
    # The first deployment opens the settings page before API credentials are supplied.
    # Cloud activities already report llm_not_configured / vlm_not_configured safely.
    if not settings.detector_path.is_file() or settings.detector_path.stat().st_size == 0:
        raise ContainerError("detector weights are missing or empty")
    try:
        errors = _model_errors(Path(os.environ.get("SEWER_MODEL_ROOT", "/models")))
    except Exception:
        raise ContainerError("model bundle validation failed; run model_bundle.py check") from None
    if errors:
        raise ContainerError("model bundle is incomplete; run model_bundle.py check")
    try:
        build_geometry_config(settings, GeometryOptions(operation="relative_measure"))
    except Exception:
        raise ContainerError("geometry model files or pinned provenance are invalid") from None
    if role == "api" and (
        settings.web_dist is None or not (settings.web_dist / "index.html").is_file()
    ):
        raise ContainerError("built frontend is missing from the image")
    if role == "gpu":
        validate_gpu(settings)


def validate_gpu(settings: Settings) -> None:
    try:
        import torch

        if not torch.cuda.is_available():
            raise ValueError
        for value in (
            settings.detector_device,
            settings.enhancement_device,
            settings.geometry_device,
        ):
            device = torch.device(f"cuda:{value}" if str(value).isdigit() else value)
            if device.type != "cuda":
                raise ValueError
            index = 0 if device.index is None else device.index
            if index >= torch.cuda.device_count():
                raise ValueError
    except Exception:
        raise ContainerError(
            "GPU unavailable; check NVIDIA runtime and configured device IDs"
        ) from None


def database_ready(settings: Settings, *, require_tables: bool) -> bool:
    try:
        with psycopg.connect(
            settings.database_dsn.get_secret_value(),
            connect_timeout=int(PROBE_TIMEOUT),
            options="-c statement_timeout=2000",
        ) as connection:
            if not require_tables:
                return connection.execute("SELECT 1").fetchone() == (1,)
            rows = connection.execute(
                "SELECT tablename FROM pg_catalog.pg_tables "
                "WHERE schemaname = 'public' AND tablename = ANY(%s)",
                (list(REQUIRED_TABLES),),
            ).fetchall()
            return set(REQUIRED_TABLES).issubset({row[0] for row in rows})
    except Exception:
        return False


async def _temporal_probe(settings: Settings) -> bool:
    from temporalio.api.workflowservice.v1 import DescribeNamespaceRequest
    from temporalio.client import Client

    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    if not await client.service_client.check_health(timeout=timedelta(seconds=2)):
        return False
    await client.service_client.workflow_service.describe_namespace(
        DescribeNamespaceRequest(namespace=settings.temporal_namespace),
        timeout=timedelta(seconds=2),
    )
    return True


def temporal_ready(settings: Settings) -> bool:
    try:
        return asyncio.run(asyncio.wait_for(_temporal_probe(settings), timeout=PROBE_TIMEOUT))
    except Exception:
        return False


def wait_dependencies(settings: Settings, *, require_tables: bool, timeout=STARTUP_TIMEOUT) -> None:
    deadline = time.monotonic() + timeout
    while True:
        if database_ready(settings, require_tables=require_tables) and temporal_ready(settings):
            return
        if time.monotonic() >= deadline:
            raise ContainerError(
                "database or Temporal namespace was not ready before startup timeout"
            )
        time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))


def ready_file(role: str) -> Path:
    return Path(f"/tmp/sewer-{role}.ready.json")


def worker_command(role: str) -> list[str]:
    return [sys.executable, str(WORKER), role, "--ready-file", str(ready_file(role))]


def worker_ready(role: str) -> bool:
    try:
        marker = ready_file(role)
        if marker.is_symlink():
            return False
        record = json.loads(marker.read_text(encoding="utf-8"))
        pid = record.get("pid")
        if type(pid) is not int or pid <= 0 or record != {"pid": pid, "kind": role}:
            return False
        process = Path("/proc") / str(pid)
        stat = (process / "stat").read_text().rpartition(") ")[2].split()
        if not stat or stat[0] == "Z":
            return False
        command = [
            os.fsdecode(arg) for arg in (process / "cmdline").read_bytes().split(b"\0") if arg
        ]
        if command != worker_command(role):
            return False
        boot = next(
            int(line.split()[1])
            for line in Path("/proc/stat").read_text().splitlines()
            if line.startswith("btime ")
        )
        started = boot + int(stat[19]) / os.sysconf("SC_CLK_TCK")
        return marker.stat().st_mtime >= started
    except (OSError, ValueError, TypeError, KeyError, IndexError, StopIteration, AttributeError):
        return False


def clear_stale_marker(role: str) -> None:
    marker = ready_file(role)
    if marker.is_symlink():
        raise ContainerError("worker ready marker must not be a symlink")
    if not marker.exists():
        return
    if worker_ready(role):
        raise ContainerError("a worker for this role is already running")
    if not marker.is_file() or marker.stat().st_uid != os.getuid():
        raise ContainerError("worker ready marker is not owned by this process user")
    marker.unlink()


def api_ready() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=1) as response:
            payload = json.load(response)
            return response.status == 200 and payload.get("status") == "alive"
    except Exception:
        return False


def healthcheck(settings: Settings, role: str) -> bool:
    process_ready = api_ready() if role == "api" else worker_ready(role)
    return (
        process_ready and database_ready(settings, require_tables=True) and temporal_ready(settings)
    )


def launch(settings: Settings, role: str) -> None:
    validate_configuration(settings, role)
    wait_dependencies(settings, require_tables=role != "initialize")
    if role == "initialize":
        try:
            HumanReviewRepository(settings.database_dsn).initialize()
        except Exception:
            raise ContainerError("application database initialization failed") from None
        if not database_ready(settings, require_tables=True):
            raise ContainerError("application schema is incomplete after initialization")
        print("application database initialized", flush=True)
        return
    try:
        settings.asset_root.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise ContainerError("media volume is not writable by the container user") from None
    if not os.access(settings.asset_root, os.W_OK | os.X_OK):
        raise ContainerError("media volume is not writable by the container user")
    if role == "api":
        command = [
            sys.executable,
            "-m",
            "uvicorn",
            "pipe_inspector.api.app:create_app",
            "--factory",
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
        ]
    else:
        clear_stale_marker(role)
        command = worker_command(role)
    # exec preserves container signal delivery to uvicorn / the worker's drain handler.
    os.chdir(ROOT)
    os.execv(command[0], command)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("initialize", "api", "cpu", "gpu", "check"))
    parser.add_argument("check_role", nargs="?", choices=("api", "cpu", "gpu"))
    args = parser.parse_args(argv)
    if (args.role == "check") != (args.check_role is not None):
        parser.error("check requires exactly one service role; other commands take no extra role")
    try:
        settings = configure_environment()
        if args.role == "check":
            return 0 if healthcheck(settings, args.check_role) else 1
        launch(settings, args.role)
        return 0
    except ContainerError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except Exception:
        print(
            "error: container startup failed; check configuration and dependencies", file=sys.stderr
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
