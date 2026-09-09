#!/usr/bin/env python3
"""Project-local control for the native PostgreSQL + Temporal runtime."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = REPO_ROOT / ".tools" / "temporal"
TEMPORAL_SERVER = TOOLS_DIR / "temporal-server"
TEMPORAL_SQL_TOOL = TOOLS_DIR / "temporal-sql-tool"
DYNAMIC_CONFIG = TOOLS_DIR / "config" / "dynamicconfig" / "development-sql.yaml"
SCHEMA_ROOT = TOOLS_DIR / "schema" / "postgresql" / "v12"
RUNTIME_DIR = REPO_ROOT / "var" / "runtime"
CREDENTIALS_FILE = RUNTIME_DIR / "credentials.env"
TEMPORAL_CONFIG = RUNTIME_DIR / "temporal.yaml"
TEMPORAL_PID = RUNTIME_DIR / "temporal.pid"
TEMPORAL_LOG = RUNTIME_DIR / "temporal.log"
ENV_FILE = REPO_ROOT / ".env"
POSTGRES_DATA_DIR = Path("/var/lib/postgresql/14/main")
POSTGRES_CONTROL = [
    "/usr/bin/pg_ctlcluster",
    "--skip-systemctl-redirect",
    "14",
    "main",
]
SOURCE_URL = "https://github.com/temporalio/temporal/archive/refs/tags/v1.31.2.tar.gz"
ENV_BEGIN = "# BEGIN sewer-agent managed runtime"
ENV_END = "# END sewer-agent managed runtime"
SECRET_KEYS = ("SEWER_TEMPORAL_DB_PASSWORD", "SEWER_APP_DB_PASSWORD")


class RuntimeControlError(RuntimeError):
    """An operational error safe to show without command output or secrets."""


def _atomic_write(path: Path, content: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _parse_env(content: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line in content.splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        parsed[key] = value
    return parsed


def load_or_create_credentials(runtime_dir: Path = RUNTIME_DIR) -> dict[str, str]:
    runtime_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(runtime_dir, 0o700)
    credential_path = runtime_dir / "credentials.env"
    if credential_path.exists():
        credentials = _parse_env(credential_path.read_text(encoding="utf-8"))
        if any(not credentials.get(key) for key in SECRET_KEYS):
            raise RuntimeControlError(f"incomplete credentials file: {credential_path}")
        os.chmod(credential_path, 0o600)
        return {key: credentials[key] for key in SECRET_KEYS}

    credentials = {key: secrets.token_urlsafe(32) for key in SECRET_KEYS}
    content = "".join(f"{key}={credentials[key]}\n" for key in SECRET_KEYS)
    _atomic_write(credential_path, content)
    return credentials


def write_managed_env(path: Path, values: dict[str, str]) -> None:
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    block = (
        ENV_BEGIN
        + "\n"
        + "".join(f"{key}={value}\n" for key, value in values.items())
        + ENV_END
        + "\n"
    )
    start = original.find(ENV_BEGIN)
    if start >= 0:
        end = original.find(ENV_END, start)
        if end < 0:
            raise RuntimeControlError(f"unterminated managed block in {path}")
        end += len(ENV_END)
        if end < len(original) and original[end] == "\n":
            end += 1
        content = original[:start] + block + original[end:]
    else:
        separator = "" if not original or original.endswith("\n") else "\n"
        content = original + separator + block
    _atomic_write(path, content)


def render_temporal_config(password: str, dynamic_config: Path = DYNAMIC_CONFIG) -> str:
    secret = json.dumps(password)
    dynamic_path = json.dumps(str(dynamic_config.resolve()))
    return f"""log:
  stdout: true
  level: info
persistence:
  defaultStore: postgres-default
  visibilityStore: postgres-visibility
  numHistoryShards: 4
  datastores:
    postgres-default:
      sql:
        pluginName: "postgres12"
        databaseName: "temporal"
        connectAddr: "127.0.0.1:5432"
        connectProtocol: "tcp"
        user: "sewer_temporal"
        password: {secret}
        maxConns: 20
        maxIdleConns: 20
        maxConnLifetime: "1h"
    postgres-visibility:
      sql:
        pluginName: "postgres12"
        databaseName: "temporal_visibility"
        connectAddr: "127.0.0.1:5432"
        connectProtocol: "tcp"
        user: "sewer_temporal"
        password: {secret}
        maxConns: 2
        maxIdleConns: 2
        maxConnLifetime: "1h"
global:
  membership:
    maxJoinDuration: 30s
    broadcastAddress: "127.0.0.1"
services:
  frontend:
    rpc:
      grpcPort: 7233
      membershipPort: 6933
      bindOnIP: "127.0.0.1"
      httpPort: 7243
  matching:
    rpc:
      grpcPort: 7235
      membershipPort: 6935
      bindOnIP: "127.0.0.1"
  history:
    rpc:
      grpcPort: 7234
      membershipPort: 6934
      bindOnIP: "127.0.0.1"
  worker:
    rpc:
      grpcPort: 7239
      membershipPort: 6939
      bindOnIP: "127.0.0.1"
clusterMetadata:
  enableGlobalNamespace: false
  failoverVersionIncrement: 10
  masterClusterName: "active"
  currentClusterName: "active"
  clusterInformation:
    active:
      enabled: true
      initialFailoverVersion: 1
      rpcName: "frontend"
      rpcAddress: "127.0.0.1:7233"
dcRedirectionPolicy:
  policy: "noop"
dynamicConfigClient:
  filepath: {dynamic_path}
  pollInterval: "10s"
"""


def _run(
    command: list[str],
    *,
    input_text: str | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=REPO_ROOT,
        input=input_text,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise RuntimeControlError(f"{Path(command[0]).name} failed with exit {result.returncode}")
    return result


def postgres_running() -> bool:
    return _run([*POSTGRES_CONTROL, "status"], check=False).returncode == 0


def ensure_postgres_running() -> None:
    if not postgres_running():
        _run([*POSTGRES_CONTROL, "start"])


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _run_postgres_sql(sql: str, database: str = "postgres") -> str:
    command = [
        "/usr/sbin/runuser",
        "-u",
        "postgres",
        "--",
        "/usr/bin/psql",
        "--no-psqlrc",
        "--quiet",
        "--tuples-only",
        "--no-align",
        "--set",
        "ON_ERROR_STOP=1",
        "--dbname",
        database,
    ]
    return _run(command, input_text=sql).stdout.strip()


def _create_roles_and_databases(credentials: dict[str, str]) -> None:
    temporal_password = _sql_literal(credentials["SEWER_TEMPORAL_DB_PASSWORD"])
    app_password = _sql_literal(credentials["SEWER_APP_DB_PASSWORD"])
    sql = rf"""
DO $runtime$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sewer_temporal') THEN
    ALTER ROLE sewer_temporal LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
    ALTER ROLE sewer_temporal PASSWORD {temporal_password};
  ELSE
    CREATE ROLE sewer_temporal LOGIN PASSWORD {temporal_password};
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sewer_agent') THEN
    ALTER ROLE sewer_agent LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {app_password};
  ELSE
    CREATE ROLE sewer_agent LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {app_password};
  END IF;
END
$runtime$;
SELECT 'CREATE DATABASE temporal OWNER sewer_temporal'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'temporal')\gexec
SELECT 'CREATE DATABASE temporal_visibility OWNER sewer_temporal'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'temporal_visibility')\gexec
SELECT 'CREATE DATABASE sewer_agent OWNER sewer_agent'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'sewer_agent')\gexec
ALTER DATABASE temporal OWNER TO sewer_temporal;
ALTER DATABASE temporal_visibility OWNER TO sewer_temporal;
ALTER DATABASE sewer_agent OWNER TO sewer_agent;
"""
    _run_postgres_sql(sql)


def _schema_installed(database: str) -> bool:
    result = _run_postgres_sql(
        "SELECT to_regclass('public.schema_version') IS NOT NULL;\n",
        database=database,
    )
    return result == "t"


def _ensure_schema_files() -> None:
    core = SCHEMA_ROOT / "temporal" / "versioned"
    visibility = SCHEMA_ROOT / "visibility" / "versioned"
    if core.is_dir() and visibility.is_dir():
        return

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, archive_name = tempfile.mkstemp(dir=RUNTIME_DIR, suffix=".tar.gz")
    archive = Path(archive_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            try:
                with urllib.request.urlopen(SOURCE_URL, timeout=60) as response:
                    while chunk := response.read(1024 * 1024):
                        output.write(chunk)
            except (OSError, urllib.error.URLError) as error:
                raise RuntimeControlError("failed to fetch Temporal v1.31.2 schema") from error
        prefix = "temporal-1.31.2/schema/postgresql/v12/"
        with tarfile.open(archive, "r:gz") as source:
            members = [
                member
                for member in source.getmembers()
                if member.isfile() and member.name.startswith(prefix)
            ]
            if not members:
                raise RuntimeControlError(
                    "Temporal source archive contains no PostgreSQL v12 schema"
                )
            for member in members:
                relative = Path(member.name.removeprefix(prefix))
                target = SCHEMA_ROOT / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                extracted = source.extractfile(member)
                if extracted is None:
                    raise RuntimeControlError(f"could not extract schema member {member.name}")
                with extracted, target.open("wb") as output:
                    while chunk := extracted.read(1024 * 1024):
                        output.write(chunk)
    finally:
        archive.unlink(missing_ok=True)


def _update_temporal_schemas(password: str) -> None:
    environment = os.environ.copy()
    environment["SQL_PASSWORD"] = password
    base = [
        str(TEMPORAL_SQL_TOOL),
        "--plugin",
        "postgres12",
        "--endpoint",
        "127.0.0.1",
        "--port",
        "5432",
        "--user",
        "sewer_temporal",
    ]
    for database, schema in (
        ("temporal", SCHEMA_ROOT / "temporal" / "versioned"),
        ("temporal_visibility", SCHEMA_ROOT / "visibility" / "versioned"),
    ):
        if not _schema_installed(database):
            _run([*base, "--database", database, "setup-schema", "-v", "0.0"], env=environment)
        _run(
            [*base, "--database", database, "update-schema", "--schema-dir", str(schema)],
            env=environment,
        )


def initialize() -> dict[str, object]:
    if not TEMPORAL_SERVER.is_file() or not TEMPORAL_SQL_TOOL.is_file():
        raise RuntimeControlError("Temporal v1.31.2 server tools are missing")
    if not DYNAMIC_CONFIG.is_file():
        raise RuntimeControlError(f"missing dynamic config: {DYNAMIC_CONFIG}")
    credentials = load_or_create_credentials()
    ensure_postgres_running()
    _create_roles_and_databases(credentials)
    _ensure_schema_files()
    _update_temporal_schemas(credentials["SEWER_TEMPORAL_DB_PASSWORD"])
    _atomic_write(
        TEMPORAL_CONFIG,
        render_temporal_config(credentials["SEWER_TEMPORAL_DB_PASSWORD"]),
    )
    managed_env = {
        "SEWER_DATABASE_DSN": (
            "postgresql://sewer_agent:"
            + quote(credentials["SEWER_APP_DB_PASSWORD"], safe="")
            + "@127.0.0.1:5432/sewer_agent"
        ),
        "SEWER_TEMPORAL_ADDRESS": "127.0.0.1:7233",
        "SEWER_TEMPORAL_NAMESPACE": "default",
    }
    write_managed_env(ENV_FILE, managed_env)
    return {
        "initialized": True,
        "postgres": "running",
        "postgres_data_dir": str(POSTGRES_DATA_DIR),
        "temporal_config": str(TEMPORAL_CONFIG),
    }


def _pid_alive(pid: int) -> bool:
    try:
        state = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()[2]
        return state != "Z"
    except (FileNotFoundError, PermissionError, IndexError):
        return False


def process_matches(pid: int, executable: Path, config: Path) -> bool:
    if not _pid_alive(pid):
        return False
    try:
        actual_executable = Path(f"/proc/{pid}/exe").resolve()
        arguments = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except (FileNotFoundError, PermissionError, OSError):
        return False
    expected_executable = executable.resolve()
    expected_config = os.fsencode(str(config.resolve()))
    has_config = any(
        argument == b"--config-file"
        and index + 1 < len(arguments)
        and arguments[index + 1] == expected_config
        for index, argument in enumerate(arguments)
    )
    return actual_executable == expected_executable and has_config


def _read_pid(pid_file: Path) -> int:
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError) as error:
        raise RuntimeControlError(f"invalid or missing PID file: {pid_file}") from error
    if pid <= 1:
        raise RuntimeControlError(f"unsafe PID in {pid_file}")
    return pid


def stop_temporal(
    pid_file: Path = TEMPORAL_PID,
    executable: Path = TEMPORAL_SERVER,
    config: Path = TEMPORAL_CONFIG,
    *,
    timeout: float = 15.0,
) -> bool:
    if not pid_file.exists():
        return False
    pid = _read_pid(pid_file)
    if not _pid_alive(pid):
        pid_file.unlink(missing_ok=True)
        return False
    if not process_matches(pid, executable, config):
        raise RuntimeControlError(f"PID {pid} identity does not match the managed Temporal process")
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and _pid_alive(pid):
        time.sleep(0.05)
    if _pid_alive(pid):
        if not process_matches(pid, executable, config):
            raise RuntimeControlError(f"PID {pid} changed identity while stopping")
        os.kill(pid, signal.SIGKILL)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and _pid_alive(pid):
            time.sleep(0.05)
    if _pid_alive(pid):
        raise RuntimeControlError(f"managed Temporal PID {pid} did not stop")
    pid_file.unlink(missing_ok=True)
    return True


async def _healthy() -> bool:
    try:
        from temporalio.client import Client

        client = await Client.connect("127.0.0.1:7233")
        return await client.service_client.check_health(timeout=timedelta(seconds=2))
    except Exception:
        return False


async def _register_default_namespace() -> None:
    from temporalio.api.workflowservice.v1 import (
        DescribeNamespaceRequest,
        RegisterNamespaceRequest,
    )
    from temporalio.client import Client
    from temporalio.service import RPCError, RPCStatusCode

    client = await Client.connect("127.0.0.1:7233")
    try:
        await client.service_client.workflow_service.describe_namespace(
            DescribeNamespaceRequest(namespace="default")
        )
    except RPCError as error:
        if error.status != RPCStatusCode.NOT_FOUND:
            raise
        await client.service_client.workflow_service.register_namespace(
            RegisterNamespaceRequest(
                namespace="default",
                workflow_execution_retention_period=timedelta(days=3),
            )
        )


def _current_temporal_pid() -> int | None:
    if not TEMPORAL_PID.exists():
        return None
    try:
        return _read_pid(TEMPORAL_PID)
    except RuntimeControlError:
        return None


def start_temporal(timeout: float = 45.0) -> dict[str, object]:
    ensure_postgres_running()
    if not TEMPORAL_CONFIG.is_file():
        raise RuntimeControlError("runtime is not initialized; run init first")
    existing = _current_temporal_pid()
    if existing is not None and _pid_alive(existing):
        if not process_matches(existing, TEMPORAL_SERVER, TEMPORAL_CONFIG):
            raise RuntimeControlError(f"PID {existing} identity does not match managed Temporal")
        if asyncio.run(_healthy()):
            return status()
        raise RuntimeControlError(f"managed Temporal PID {existing} is running but unhealthy")
    TEMPORAL_PID.unlink(missing_ok=True)

    TEMPORAL_LOG.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with TEMPORAL_LOG.open("ab") as log:
        os.chmod(TEMPORAL_LOG, 0o600)
        process = subprocess.Popen(
            [
                str(TEMPORAL_SERVER),
                "--config-file",
                str(TEMPORAL_CONFIG),
                "--allow-no-auth",
                "start",
            ],
            cwd=REPO_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    _atomic_write(TEMPORAL_PID, f"{process.pid}\n")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process_matches(process.pid, TEMPORAL_SERVER, TEMPORAL_CONFIG):
            TEMPORAL_PID.unlink(missing_ok=True)
            raise RuntimeControlError(f"Temporal exited during startup; inspect {TEMPORAL_LOG}")
        if asyncio.run(_healthy()):
            try:
                asyncio.run(_register_default_namespace())
            except Exception as error:
                stop_temporal()
                raise RuntimeControlError(
                    "Temporal started but default namespace registration failed"
                ) from error
            return status()
        time.sleep(0.5)
    stop_temporal()
    raise RuntimeControlError(f"Temporal readiness timed out; inspect {TEMPORAL_LOG}")


def status() -> dict[str, object]:
    pid = _current_temporal_pid()
    running = bool(pid and process_matches(pid, TEMPORAL_SERVER, TEMPORAL_CONFIG))
    ready = running and asyncio.run(_healthy())
    return {
        "postgres": {
            "running": postgres_running(),
            "data_dir": str(POSTGRES_DATA_DIR),
        },
        "temporal": {
            "running": running,
            "ready": ready,
            "pid": pid if running else None,
            "address": "127.0.0.1:7233",
        },
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("init", "start", "stop", "status"))
    return parser


def main(argv: list[str] | None = None) -> int:
    command = _build_parser().parse_args(argv).command
    try:
        if command == "init":
            result = initialize()
        elif command == "start":
            result = start_temporal()
        elif command == "stop":
            result = {"temporal_stopped": stop_temporal(), "postgres_stopped": False}
        else:
            result = status()
    except RuntimeControlError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
