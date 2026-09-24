#!/usr/bin/env python3
"""Build and control the fixed loopback-only Sewer Agent application."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

try:
    from scripts import runtime
except ModuleNotFoundError:  # Running this file directly places scripts/ on sys.path.
    import runtime  # type: ignore[no-redef]

REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON = REPO_ROOT / ".venv" / "bin" / "python"
NODE_BIN = REPO_ROOT / ".tools" / "node" / "bin"
NPM = NODE_BIN / "npm"
WORKER_SCRIPT = REPO_ROOT / "scripts" / "inspection_worker.py"
WEB_DIST = REPO_ROOT / "frontend" / "dist"
APP_RUNTIME_DIR = REPO_ROOT / "var" / "runtime" / "app"
STATE_FILE = APP_RUNTIME_DIR / "processes.json"
LOCK_FILE = APP_RUNTIME_DIR / "control.lock"
ADDRESS = "http://127.0.0.1:8000"
SHUTDOWN_TIMEOUT = 30.0
START_ORDER = ("gpu", "cpu", "api")
WORKER_ROLES = ("cpu", "gpu")
REQUIRED_TABLES = (
    "sewer_sessions",
    "sewer_assets",
    "sewer_tasks",
    "sewer_inspection_runs",
    "sewer_tool_calls",
    "sewer_step_checkpoints",
    "sewer_conversation_turns",
    "sewer_image_supplements",
    "sewer_image_operations",
    "sewer_video_states",
    "sewer_video_chunks",
    "sewer_human_reviews",
)


class AppControlError(RuntimeError):
    """An operational error that is safe to show without configuration values."""


@dataclass(frozen=True)
class ComponentSpec:
    role: str
    command: list[str]
    log: Path


def _ready_file(role: str) -> Path:
    return APP_RUNTIME_DIR / f"{role}.ready.json"


def _component_specs() -> dict[str, ComponentSpec]:
    worker = str(WORKER_SCRIPT.resolve())
    python = str(PYTHON)
    return {
        "gpu": ComponentSpec(
            "gpu",
            [python, worker, "gpu", "--ready-file", str(_ready_file("gpu").resolve())],
            APP_RUNTIME_DIR / "gpu.log",
        ),
        "cpu": ComponentSpec(
            "cpu",
            [python, worker, "cpu", "--ready-file", str(_ready_file("cpu").resolve())],
            APP_RUNTIME_DIR / "cpu.log",
        ),
        "api": ComponentSpec(
            "api",
            [
                python,
                "-m",
                "uvicorn",
                "pipe_inspector.api.app:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                "8000",
            ],
            APP_RUNTIME_DIR / "api.log",
        ),
    }


def _ensure_runtime_dir() -> None:
    APP_RUNTIME_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(APP_RUNTIME_DIR, 0o700)


def _atomic_write_json(path: Path, value: object) -> None:
    _ensure_runtime_dir()
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def _operation_lock() -> Iterator[None]:
    _ensure_runtime_dir()
    descriptor = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.chmod(LOCK_FILE, 0o600)
        with os.fdopen(descriptor, "r+"):
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise


def _valid_record(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    pid = value.get("pid")
    start_ticks = value.get("start_ticks")
    command = value.get("command")
    return (
        isinstance(pid, int)
        and not isinstance(pid, bool)
        and pid > 1
        and isinstance(start_ticks, int)
        and not isinstance(start_ticks, bool)
        and start_ticks >= 0
        and isinstance(command, list)
        and bool(command)
        and all(isinstance(argument, str) and argument for argument in command)
    )


def _read_records() -> dict[str, dict[str, object]]:
    if not STATE_FILE.exists():
        return {}
    try:
        value = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AppControlError("application process record is unreadable") from error
    if not isinstance(value, dict) or not all(
        isinstance(role, str) and _valid_record(record) for role, record in value.items()
    ):
        raise AppControlError("application process record is invalid")
    return value


def _write_records(records: dict[str, dict[str, object]]) -> None:
    if records:
        _atomic_write_json(STATE_FILE, records)
    else:
        STATE_FILE.unlink(missing_ok=True)


def _process_snapshot(pid: int) -> dict[str, object] | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        fields = stat.rpartition(") ")[2].split()
        if len(fields) <= 19 or fields[0] == "Z":
            return None
        command = [
            os.fsdecode(argument)
            for argument in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
            if argument
        ]
        if not command:
            return None
        return {"start_ticks": int(fields[19]), "command": command}
    except (FileNotFoundError, PermissionError, OSError, ValueError):
        return None


def _pid_alive(pid: int) -> bool:
    return _process_snapshot(pid) is not None


def _record_matches(record: dict[str, object], role: str) -> bool:
    spec = _component_specs().get(role)
    if spec is None or not _valid_record(record) or record["command"] != spec.command:
        return False
    snapshot = _process_snapshot(record["pid"])
    return bool(
        snapshot
        and snapshot["start_ticks"] == record["start_ticks"]
        and snapshot["command"] == spec.command
    )


def _worker_ready(role: str, record: dict[str, object]) -> bool:
    try:
        marker = json.loads(_ready_file(role).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return False
    return marker == {"pid": record["pid"], "kind": role}


def _api_ready() -> bool:
    try:
        with urllib.request.urlopen(f"{ADDRESS}/health", timeout=1) as response:
            payload = json.load(response)
        return response.status == 200 and payload.get("status") == "alive"
    except (OSError, urllib.error.URLError, ValueError, json.JSONDecodeError):
        return False


def _component_ready(role: str, record: dict[str, object]) -> bool:
    if not _record_matches(record, role):
        return False
    return _api_ready() if role == "api" else _worker_ready(role, record)


def _port_available() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", 8000))
        except OSError:
            return False
    return True


def _component_environment(role: str) -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("SEWER_WEB_DIST", None)
    if role == "api":
        environment["SEWER_WEB_DIST"] = str(WEB_DIST.resolve())
    return environment


def _load_settings():
    try:
        from pipe_inspector.config import Settings

        return Settings(_env_file=REPO_ROOT / ".env")
    except Exception as error:
        raise AppControlError("application configuration is invalid; check .env") from error


def _storage_state(settings) -> dict[str, bool]:
    try:
        import psycopg

        with psycopg.connect(
            settings.database_dsn.get_secret_value(), connect_timeout=5
        ) as connection:
            rows = connection.execute(
                "SELECT tablename FROM pg_catalog.pg_tables "
                "WHERE schemaname = 'public' AND tablename = ANY(%s)",
                (list(REQUIRED_TABLES),),
            ).fetchall()
    except Exception:
        return {"reachable": False, "tables_ready": False}
    present = {row[0] for row in rows}
    return {
        "reachable": True,
        "tables_ready": not bool(set(REQUIRED_TABLES) - present),
    }


def _preflight() -> None:
    settings = _load_settings()
    if not settings.llm_configured or not settings.vlm_configured:
        raise AppControlError("LLM and VLM configuration is incomplete; check .env.example")
    checkpoint = settings.detector_path
    if not checkpoint.is_absolute():
        checkpoint = REPO_ROOT / checkpoint
    if not checkpoint.is_file():
        raise AppControlError("primary detector weights are missing")
    storage = _storage_state(settings)
    if not storage["reachable"]:
        raise AppControlError("application storage is unavailable")
    if not storage["tables_ready"]:
        raise AppControlError("application tables are missing; run scripts/init_storage.py")


def _start_persistent_runtime() -> None:
    try:
        runtime.start_temporal()
    except runtime.RuntimeControlError as error:
        raise AppControlError(str(error)) from error
    except Exception as error:
        raise AppControlError("persistent runtime startup failed") from error


def _persistent_status() -> dict[str, object]:
    try:
        result = runtime.status()
    except Exception as error:
        raise AppControlError("persistent runtime status is unavailable") from error
    postgres = dict(result["postgres"])
    try:
        storage = _storage_state(_load_settings())
    except AppControlError:
        storage = {"reachable": False, "tables_ready": False}
    postgres["ready"] = bool(
        postgres.get("running") and storage["reachable"] and storage["tables_ready"]
    )
    return {"postgres": postgres, "temporal": result["temporal"]}


def _spawn_component(role: str, spec: ComponentSpec) -> subprocess.Popen[bytes]:
    _ensure_runtime_dir()
    descriptor = os.open(spec.log, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
    try:
        os.chmod(spec.log, 0o600)
        return subprocess.Popen(
            spec.command,
            cwd=REPO_ROOT,
            env=_component_environment(role),
            stdin=subprocess.DEVNULL,
            stdout=descriptor,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    finally:
        os.close(descriptor)


def _new_record(process: subprocess.Popen, spec: ComponentSpec) -> dict[str, object]:
    snapshot = _process_snapshot(process.pid)
    if snapshot is None:
        raise AppControlError(f"{spec.role} exited during startup; inspect {spec.log}")
    return {
        "pid": process.pid,
        "start_ticks": snapshot["start_ticks"],
        "command": spec.command,
    }


def _remove_ready_marker(role: str, pid: int) -> None:
    marker = _ready_file(role)
    try:
        value = json.loads(marker.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return
    if value == {"pid": pid, "kind": role}:
        marker.unlink(missing_ok=True)


def _reconcile_records(
    records: dict[str, dict[str, object]], specs: dict[str, ComponentSpec]
) -> None:
    unknown = set(records) - set(specs)
    if unknown:
        raise AppControlError("application process record contains an unknown component")
    changed = False
    for role, record in list(records.items()):
        if _process_snapshot(record["pid"]) is None:
            _remove_ready_marker(role, record["pid"])
            del records[role]
            changed = True
        elif not _record_matches(record, role):
            raise AppControlError(f"{role} process identity does not match its record")
    if changed:
        _write_records(records)


def _status_locked(
    records: dict[str, dict[str, object]] | None = None,
) -> dict[str, object]:
    specs = _component_specs()
    records = _read_records() if records is None else records
    components: dict[str, dict[str, object]] = {}
    for role, spec in specs.items():
        record = records.get(role)
        running = bool(record and _record_matches(record, role))
        components[role] = {
            "running": running,
            "ready": bool(running and _component_ready(role, record)),
            "pid": record["pid"] if running else None,
            "log": str(spec.log),
        }
    dependencies = _persistent_status()
    ready = (
        all(component["ready"] for component in components.values())
        and bool(dependencies["postgres"].get("ready"))
        and bool(dependencies["temporal"].get("ready"))
    )
    return {
        "ready": ready,
        "address": ADDRESS,
        "log_dir": str(APP_RUNTIME_DIR),
        "components": components,
        "dependencies": dependencies,
    }


def status() -> dict[str, object]:
    with _operation_lock():
        return _status_locked()


def _signal_exact(record: dict[str, object], role: str) -> None:
    if not _record_matches(record, role):
        raise AppControlError(f"{role} process identity does not match its record")
    try:
        os.kill(record["pid"], signal.SIGTERM)
    except ProcessLookupError:
        pass
    except OSError as error:
        raise AppControlError(f"could not stop managed {role} process") from error


def _wait_for_exit(record: dict[str, object], deadline: float) -> bool:
    while time.monotonic() < deadline:
        snapshot = _process_snapshot(record["pid"])
        if snapshot is None or snapshot["start_ticks"] != record["start_ticks"]:
            return True
        time.sleep(0.05)
    snapshot = _process_snapshot(record["pid"])
    return snapshot is None or snapshot["start_ticks"] != record["start_ticks"]


def _stop_locked(timeout: float) -> dict[str, object]:
    records = _read_records()
    specs = _component_specs()
    _reconcile_records(records, specs)
    for role, record in records.items():
        if not _record_matches(record, role):
            raise AppControlError(f"{role} process identity does not match its record")

    deadline = time.monotonic() + timeout
    stopped: list[str] = []
    api = records.get("api")
    if api is not None:
        _signal_exact(api, "api")
        if _wait_for_exit(api, deadline):
            stopped.append("api")
            del records["api"]
            _write_records(records)

    workers = [(role, records[role]) for role in WORKER_ROLES if role in records]
    for role, record in workers:
        _signal_exact(record, role)
    for role, record in workers:
        if _wait_for_exit(record, deadline):
            stopped.append(role)
            records.pop(role, None)
            _remove_ready_marker(role, record["pid"])
            _write_records(records)

    remaining = [role for role in specs if role in records]
    if remaining:
        _write_records(records)
        raise AppControlError("managed component did not stop within 30 seconds")
    return {"stopped": stopped, "persistent_services_stopped": False}


def stop(timeout: float = SHUTDOWN_TIMEOUT) -> dict[str, object]:
    with _operation_lock():
        return _stop_locked(timeout)


def _cleanup_created(
    records: dict[str, dict[str, object]], created: list[str], timeout: float = SHUTDOWN_TIMEOUT
) -> list[str]:
    targets = [(role, records[role]) for role in reversed(created) if role in records]
    for role, record in targets:
        if _record_matches(record, role):
            _signal_exact(record, role)
    deadline = time.monotonic() + timeout
    remaining: list[str] = []
    for role, record in targets:
        if _wait_for_exit(record, deadline):
            records.pop(role, None)
            _remove_ready_marker(role, record["pid"])
        else:
            remaining.append(role)
    _write_records(records)
    return remaining


def _start_locked(timeout: float) -> dict[str, object]:
    if not PYTHON.is_file():
        raise AppControlError("project virtual environment is missing")
    if not (WEB_DIST / "index.html").is_file():
        raise AppControlError("frontend build is missing; run scripts/app.py build")
    _start_persistent_runtime()
    _preflight()

    specs = _component_specs()
    records = _read_records()
    _reconcile_records(records, specs)
    if "api" not in records and not _port_available():
        raise AppControlError("127.0.0.1:8000 is already in use")

    created: list[str] = []
    try:
        for role in START_ORDER:
            if role in records:
                continue
            process = _spawn_component(role, specs[role])
            record = _new_record(process, specs[role])
            records[role] = record
            created.append(role)
            _write_records(records)

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if all(_component_ready(role, records[role]) for role in START_ORDER):
                return _status_locked(records)
            for role, record in records.items():
                if not _record_matches(record, role):
                    raise AppControlError(
                        f"{role} exited during startup; inspect {specs[role].log}"
                    )
            time.sleep(0.05)
        raise AppControlError("application readiness timed out; inspect var/runtime/app logs")
    except Exception as error:
        remaining = _cleanup_created(records, created)
        if remaining:
            raise AppControlError("startup failed and a new component did not stop") from error
        if isinstance(error, AppControlError):
            raise
        raise AppControlError("application startup failed; inspect var/runtime/app logs") from error


def start(timeout: float = 45.0) -> dict[str, object]:
    with _operation_lock():
        return _start_locked(timeout)


def restart(timeout: float = 45.0) -> dict[str, object]:
    with _operation_lock():
        _stop_locked(SHUTDOWN_TIMEOUT)
        return _start_locked(timeout)


def build() -> dict[str, object]:
    if not NPM.is_file() or not (NODE_BIN / "node").is_file():
        raise AppControlError("project-local Node/npm is missing")
    environment = os.environ.copy()
    environment["PATH"] = str(NODE_BIN) + os.pathsep + environment.get("PATH", "")
    result = subprocess.run(
        [str(NPM), "--prefix", "frontend", "run", "build"],
        cwd=REPO_ROOT,
        env=environment,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0 or not (WEB_DIST / "index.html").is_file():
        raise AppControlError("frontend build failed")
    return {"built": True, "web_dist": str(WEB_DIST.resolve())}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("build", "start", "status", "stop", "restart"))
    return parser


def main(argv: list[str] | None = None) -> int:
    command = _build_parser().parse_args(argv).command
    try:
        result = {
            "build": build,
            "start": start,
            "status": status,
            "stop": stop,
            "restart": restart,
        }[command]()
    except AppControlError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except Exception:
        print("error: application control failed", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
