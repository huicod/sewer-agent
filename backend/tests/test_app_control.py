"""Lifecycle tests for the fixed local application process set."""

from __future__ import annotations

import importlib.util
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

APP_PATH = Path(__file__).parents[2] / "scripts" / "app.py"


@pytest.fixture
def app_control(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    if not APP_PATH.exists():
        pytest.fail("scripts/app.py is not implemented")
    monkeypatch.syspath_prepend(str(APP_PATH.parent))
    spec = importlib.util.spec_from_file_location("sewer_app_control", APP_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    runtime_dir = tmp_path / "runtime"
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("fixture", encoding="utf-8")
    monkeypatch.setattr(module, "APP_RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(module, "STATE_FILE", runtime_dir / "processes.json")
    monkeypatch.setattr(module, "LOCK_FILE", runtime_dir / "control.lock")
    monkeypatch.setattr(module, "WEB_DIST", dist)
    monkeypatch.setattr(module, "_load_settings", lambda: object())
    monkeypatch.setattr(
        module,
        "_storage_state",
        lambda settings: {"reachable": True, "tables_ready": True},
    )
    return module


def _sleep_command(*, ignore_term: bool = False, label: str = "fixture") -> list[str]:
    setup = "signal.signal(signal.SIGTERM, signal.SIG_IGN);" if ignore_term else ""
    return [
        sys.executable,
        "-c",
        f"import signal,time;{setup}print('ready', flush=True);time.sleep(30)",
        label,
    ]


def _spawn_sleep(command: list[str]) -> subprocess.Popen[str]:
    process = subprocess.Popen(command, stdout=subprocess.PIPE, text=True)
    assert process.stdout is not None
    assert process.stdout.readline().strip() == "ready"
    return process


def _record_for(app_control, pid: int, command: list[str]) -> dict[str, object]:
    snapshot = app_control._process_snapshot(pid)
    assert snapshot is not None
    return {
        "pid": pid,
        "start_ticks": snapshot["start_ticks"],
        "command": command,
    }


def _set_specs(app_control, commands: dict[str, list[str]], monkeypatch) -> None:
    monkeypatch.setattr(
        app_control,
        "_component_specs",
        lambda: {
            role: app_control.ComponentSpec(
                role, command, app_control.APP_RUNTIME_DIR / f"{role}.log"
            )
            for role, command in commands.items()
        },
    )


def _terminate_for_test(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.kill()
    process.wait(timeout=5)


def test_stop_refuses_mismatched_command_without_signalling_process(
    app_control, monkeypatch
) -> None:
    child = _spawn_sleep(_sleep_command(label="actual"))
    expected = _sleep_command(label="expected")
    _set_specs(app_control, {"cpu": expected}, monkeypatch)
    app_control._write_records({"cpu": _record_for(app_control, child.pid, expected)})

    try:
        with pytest.raises(app_control.AppControlError, match="identity"):
            app_control.stop(timeout=0.1)
        assert child.poll() is None
        assert "cpu" in app_control._read_records()
    finally:
        _terminate_for_test(child)


def test_stop_terminates_exact_process_and_removes_record(app_control, monkeypatch) -> None:
    command = _sleep_command()
    child = _spawn_sleep(command)
    _set_specs(app_control, {"cpu": command}, monkeypatch)
    app_control._write_records({"cpu": _record_for(app_control, child.pid, command)})

    try:
        result = app_control.stop(timeout=2)
        child.wait(timeout=2)
        assert result["stopped"] == ["cpu"]
        assert app_control._read_records() == {}
    finally:
        _terminate_for_test(child)


def test_stop_timeout_is_non_destructive_and_preserves_record(app_control, monkeypatch) -> None:
    command = _sleep_command(ignore_term=True)
    child = _spawn_sleep(command)
    _set_specs(app_control, {"cpu": command}, monkeypatch)
    app_control._write_records({"cpu": _record_for(app_control, child.pid, command)})

    try:
        with pytest.raises(app_control.AppControlError, match="did not stop"):
            app_control.stop(timeout=0.1)
        assert child.poll() is None
        assert app_control._read_records()["cpu"]["pid"] == child.pid
    finally:
        _terminate_for_test(child)


def test_status_reports_owned_process_marker_and_persistent_dependencies(
    app_control, monkeypatch
) -> None:
    command = _sleep_command()
    child = _spawn_sleep(command)
    _set_specs(app_control, {"cpu": command}, monkeypatch)
    app_control._write_records({"cpu": _record_for(app_control, child.pid, command)})
    marker = app_control.APP_RUNTIME_DIR / "cpu.ready.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"pid": child.pid, "kind": "cpu"}), encoding="utf-8")
    monkeypatch.setattr(
        app_control.runtime,
        "status",
        lambda: {
            "postgres": {"running": True},
            "temporal": {"running": True, "ready": True},
        },
    )

    try:
        result = app_control.status()
        assert result["components"]["cpu"] == {
            "running": True,
            "ready": True,
            "pid": child.pid,
            "log": str(app_control.APP_RUNTIME_DIR / "cpu.log"),
        }
        assert result["dependencies"]["postgres"]["running"] is True
        assert result["dependencies"]["temporal"]["ready"] is True
        assert result["ready"] is True
        assert result["address"] == "http://127.0.0.1:8000"
    finally:
        _terminate_for_test(child)


def test_status_keeps_postgres_running_distinct_from_application_storage_ready(
    app_control, monkeypatch
) -> None:
    monkeypatch.setattr(app_control, "_component_specs", lambda: {})
    monkeypatch.setattr(
        app_control.runtime,
        "status",
        lambda: {
            "postgres": {"running": True},
            "temporal": {"running": True, "ready": True},
        },
    )
    monkeypatch.setattr(app_control, "_load_settings", lambda: object())
    monkeypatch.setattr(
        app_control,
        "_storage_state",
        lambda settings: {"reachable": False, "tables_ready": False},
    )

    result = app_control.status()

    assert result["dependencies"]["postgres"] == {"running": True, "ready": False}
    assert result["ready"] is False


def test_repeated_start_reuses_owned_processes(app_control, monkeypatch) -> None:
    commands = {role: _sleep_command(label=role) for role in ("gpu", "cpu", "api")}
    _set_specs(app_control, commands, monkeypatch)
    monkeypatch.setattr(app_control, "_preflight", lambda: None)
    monkeypatch.setattr(app_control.runtime, "start_temporal", lambda: {})
    monkeypatch.setattr(
        app_control.runtime,
        "status",
        lambda: {
            "postgres": {"running": True},
            "temporal": {"running": True, "ready": True},
        },
    )
    monkeypatch.setattr(app_control, "_port_available", lambda: True)
    monkeypatch.setattr(app_control, "_component_ready", lambda role, record: True)
    snapshots: dict[int, dict[str, object]] = {}
    spawned: list[SimpleNamespace] = []

    def spawn(role, spec):
        pid = 41000 + len(spawned)
        snapshots[pid] = {"start_ticks": 9000 + len(spawned), "command": spec.command}
        process = SimpleNamespace(pid=pid)
        spawned.append(process)
        return process

    monkeypatch.setattr(app_control, "_spawn_component", spawn)
    monkeypatch.setattr(app_control, "_process_snapshot", lambda pid: snapshots.get(pid))

    first = app_control.start(timeout=0.1)
    second = app_control.start(timeout=0.1)

    assert len(spawned) == 3
    assert first["components"] == second["components"]
    assert set(app_control._read_records()) == {"gpu", "cpu", "api"}


def test_port_conflict_is_rejected_before_spawning_workers(app_control, monkeypatch) -> None:
    monkeypatch.setattr(app_control, "_preflight", lambda: None)
    monkeypatch.setattr(app_control.runtime, "start_temporal", lambda: {})
    monkeypatch.setattr(app_control, "_port_available", lambda: False)
    monkeypatch.setattr(
        app_control,
        "_spawn_component",
        lambda *args: pytest.fail("port conflict must be checked before spawning"),
    )

    with pytest.raises(app_control.AppControlError, match="8000"):
        app_control.start(timeout=0.1)


def test_start_failure_cleans_new_processes_but_keeps_existing_one(
    app_control, monkeypatch
) -> None:
    commands = {role: _sleep_command(label=role) for role in ("gpu", "cpu", "api")}
    _set_specs(app_control, commands, monkeypatch)
    existing = _spawn_sleep(commands["gpu"])
    app_control._write_records({"gpu": _record_for(app_control, existing.pid, commands["gpu"])})
    monkeypatch.setattr(app_control, "_preflight", lambda: None)
    monkeypatch.setattr(app_control.runtime, "start_temporal", lambda: {})
    monkeypatch.setattr(app_control, "_port_available", lambda: True)
    monkeypatch.setattr(
        app_control,
        "_component_ready",
        lambda role, record: role != "api" and app_control._record_matches(record, role),
    )
    spawned: list[subprocess.Popen] = []
    original_spawn = app_control._spawn_component

    def capture_spawn(role, spec):
        process = original_spawn(role, spec)
        spawned.append(process)
        return process

    monkeypatch.setattr(app_control, "_spawn_component", capture_spawn)

    try:
        with pytest.raises(app_control.AppControlError, match="readiness"):
            app_control.start(timeout=0.15)
        assert existing.poll() is None
        assert all(not app_control._pid_alive(process.pid) for process in spawned)
        assert set(app_control._read_records()) == {"gpu"}
    finally:
        _terminate_for_test(existing)
        for process in spawned:
            if app_control._pid_alive(process.pid):
                os.kill(process.pid, signal.SIGKILL)


def test_startup_cleanup_allows_worker_grace_and_keeps_timeout_records(
    app_control, monkeypatch
) -> None:
    records = {
        "gpu": {"pid": 41000, "start_ticks": 1, "command": ["existing"]},
        "cpu": {"pid": 41001, "start_ticks": 2, "command": ["created"]},
    }
    deadlines = []
    monkeypatch.setattr(app_control, "_record_matches", lambda record, role: True)
    monkeypatch.setattr(app_control, "_signal_exact", lambda record, role: None)
    monkeypatch.setattr(app_control.time, "monotonic", lambda: 100.0)

    def does_not_exit(record, deadline):
        deadlines.append(deadline)
        return False

    monkeypatch.setattr(app_control, "_wait_for_exit", does_not_exit)

    remaining = app_control._cleanup_created(records, ["cpu"])

    assert deadlines == [130.0]
    assert remaining == ["cpu"]
    assert set(app_control._read_records()) == {"gpu", "cpu"}


def test_only_api_receives_absolute_web_distribution_environment(
    app_control, monkeypatch
) -> None:
    monkeypatch.setenv("SEWER_WEB_DIST", "untrusted-parent-value")

    assert "SEWER_WEB_DIST" not in app_control._component_environment("cpu")
    assert "SEWER_WEB_DIST" not in app_control._component_environment("gpu")
    assert app_control._component_environment("api")["SEWER_WEB_DIST"] == str(
        app_control.WEB_DIST.resolve()
    )


def test_fixed_component_commands_use_project_virtual_environment(app_control) -> None:
    specs = app_control._component_specs()

    assert set(specs) == {"gpu", "cpu", "api"}
    assert all(spec.command[0] == str(app_control.PYTHON) for spec in specs.values())


def test_build_uses_project_local_npm_and_fixed_repository_cwd(app_control, monkeypatch) -> None:
    captured = {}

    def run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(app_control.subprocess, "run", run)
    result = app_control.build()

    assert captured["command"] == [str(app_control.NPM), "--prefix", "frontend", "run", "build"]
    assert captured["cwd"] == app_control.REPO_ROOT
    assert captured["env"]["PATH"].split(os.pathsep)[0] == str(app_control.NODE_BIN)
    assert result["built"] is True
