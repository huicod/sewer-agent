"""Container startup contracts without touching native services or paid APIs."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pipe_inspector.vision.geometry_models import DEPTH_ANYTHING_V2_REVISION, SAM2_REVISION
from psycopg.conninfo import conninfo_to_dict

ENTRYPOINT = Path(__file__).parents[2] / "deploy" / "docker" / "entrypoint.py"


@pytest.fixture
def entry(monkeypatch):
    spec = importlib.util.spec_from_file_location("sewer_container_entrypoint", ENTRYPOINT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for key in list(os.environ):
        if key.startswith("SEWER_") or key == "DASHSCOPE_API_KEY":
            monkeypatch.delenv(key)
    # The entrypoint also creates variables that were absent when the fixture started.
    with patch.dict(os.environ):
        yield module


@pytest.fixture
def configured(entry, monkeypatch, tmp_path):
    models = tmp_path / "models"
    detector = models / "detector" / "best.pt"
    detector.parent.mkdir(parents=True)
    detector.write_bytes(b"synthetic file-existence fixture, not a model")
    web = tmp_path / "dist"
    web.mkdir()
    (web / "index.html").write_text("test page")
    for folder, repo_id, revision, extra in (
        (
            "sam2.1-hiera-large",
            "facebook/sam2.1-hiera-large",
            SAM2_REVISION,
            ["processor_config.json", "video_preprocessor_config.json"],
        ),
        (
            "depth-anything-v2-small",
            "depth-anything/Depth-Anything-V2-Small-hf",
            DEPTH_ANYTHING_V2_REVISION,
            [],
        ),
    ):
        directory = models / "geometry" / folder
        directory.mkdir(parents=True)
        for name in ["model.safetensors", "config.json", "preprocessor_config.json", *extra]:
            (directory / name).write_text("fixture")
        (directory / "provenance.json").write_text(
            json.dumps(
                {
                    "repo_id": repo_id,
                    "revision": revision,
                }
            )
        )
    for key, value in {
        "SEWER_DB_PASSWORD": "test-password",
        "SEWER_MODEL_ROOT": str(models),
        "SEWER_ASSET_ROOT": str(tmp_path / "assets"),
        "SEWER_WEB_DIST": str(web),
        "SEWER_LLM_BASE_URL": "https://example.invalid/v1",
        "SEWER_LLM_MODEL": "test-llm",
        "SEWER_LLM_API_KEY": "test-llm-key",
        "SEWER_VLM_BASE_URL": "https://example.invalid/v1",
        "SEWER_VLM_MODEL": "test-vlm",
        "SEWER_VLM_API_KEY": "test-vlm-key",
    }.items():
        monkeypatch.setenv(key, value)
    # Model-bundle contents are tested in its own suite. Here the boundary stays explicit.
    monkeypatch.setattr(entry, "_model_errors", lambda path: [])
    return entry.configure_environment()


def test_dsn_preserves_password_characters_and_does_not_load_native_env(entry, monkeypatch):
    password = "a'b\\c space @:/?%$\nend"
    monkeypatch.setenv("SEWER_DB_PASSWORD", password)
    monkeypatch.setenv("SEWER_DB_NAME", "sewer db")
    settings = entry.configure_environment()
    parsed = conninfo_to_dict(settings.database_dsn.get_secret_value())
    assert parsed["password"] == password
    assert parsed["dbname"] == "sewer db"
    assert parsed["host"] == "postgres"
    assert settings.llm_api_key is None
    assert settings.temporal_address == "temporal:7233"
    assert settings.detector_path == Path("/models/detector/best.pt")


@pytest.mark.parametrize("port", ["0", "65536", "not-a-port"])
def test_invalid_db_port_is_reported_without_secret_values(entry, monkeypatch, port, capsys):
    monkeypatch.setenv("SEWER_DB_PASSWORD", "do-not-log-this")
    monkeypatch.setenv("SEWER_DB_PORT", port)
    assert entry.main(["api"]) == 1
    output = capsys.readouterr().err
    assert "configuration is invalid" in output
    assert "do-not-log-this" not in output


def test_missing_db_password_fails_before_connecting(entry, monkeypatch):
    monkeypatch.setattr(entry.psycopg, "connect", lambda *a, **kw: pytest.fail("connected"))
    with pytest.raises(entry.ContainerError, match="SEWER_DB_PASSWORD"):
        entry.configure_environment()


def test_compose_empty_role_keys_allow_official_qwen_shared_key(entry, monkeypatch):
    monkeypatch.setenv("SEWER_DB_PASSWORD", "db-password")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-shared-key")
    for role in ("LLM", "VLM"):
        monkeypatch.setenv(f"SEWER_{role}_API_KEY", "")
        monkeypatch.setenv(
            f"SEWER_{role}_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
        monkeypatch.setenv(f"SEWER_{role}_MODEL", "qwen3.8-max")
    settings = entry.configure_environment()
    assert settings.llm_configured and settings.vlm_configured
    assert settings.llm_api_key.get_secret_value() == "test-shared-key"
    assert settings.vlm_api_key.get_secret_value() == "test-shared-key"
    assert "SEWER_LLM_API_KEY" not in os.environ


def test_api_and_cpu_can_start_before_cloud_credentials_are_configured(entry, configured):
    settings = configured.model_copy(update={"llm_api_key": None, "vlm_api_key": None})
    entry.validate_configuration(settings, "cpu")
    entry.validate_configuration(settings, "api")


def test_missing_detector_or_incomplete_bundle_prevents_start(entry, configured, monkeypatch):
    monkeypatch.setattr(entry, "_model_errors", lambda path: ["missing enhancement checkpoint"])
    with pytest.raises(entry.ContainerError, match="bundle is incomplete"):
        entry.validate_configuration(configured, "api")
    configured.detector_path.unlink()
    with pytest.raises(entry.ContainerError, match="detector weights"):
        entry.validate_configuration(configured, "api")


def test_geometry_provenance_must_match_pinned_revision(entry, configured):
    provenance = configured.geometry_models_dir / "sam2.1-hiera-large" / "provenance.json"
    provenance.write_text(json.dumps({"repo_id": "facebook/sam2.1-hiera-large", "revision": "old"}))
    with pytest.raises(entry.ContainerError, match="pinned provenance"):
        entry.validate_configuration(configured, "cpu")


def test_cpu_and_api_preflight_do_not_probe_cuda(entry, configured, monkeypatch):
    monkeypatch.setattr(entry, "validate_gpu", lambda s: pytest.fail("CPU role used GPU"))
    entry.validate_configuration(configured, "cpu")
    entry.validate_configuration(configured, "api")


def test_gpu_preflight_refuses_unavailable_cuda(entry, configured, monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(
            cuda=SimpleNamespace(
                is_available=lambda: False,
            )
        ),
    )
    with pytest.raises(entry.ContainerError, match="GPU unavailable"):
        entry.validate_configuration(configured, "gpu")


def test_api_requires_built_frontend(entry, configured):
    (configured.web_dist / "index.html").unlink()
    with pytest.raises(entry.ContainerError, match="built frontend"):
        entry.validate_configuration(configured, "api")


def test_dependencies_wait_is_bounded(entry, configured, monkeypatch):
    monkeypatch.setattr(entry, "database_ready", lambda *a, **kw: False)
    monkeypatch.setattr(entry, "temporal_ready", lambda *a: pytest.fail("DB not ready"))
    with pytest.raises(entry.ContainerError, match="startup timeout"):
        entry.wait_dependencies(configured, require_tables=True, timeout=0)


def test_initialize_calls_existing_idempotent_repository_after_dependency_wait(
    entry,
    configured,
    monkeypatch,
):
    steps = []
    monkeypatch.setattr(entry, "wait_dependencies", lambda s, **kw: steps.append(("wait", kw)))
    monkeypatch.setattr(
        entry,
        "HumanReviewRepository",
        lambda dsn: SimpleNamespace(
            initialize=lambda: steps.append(("initialize", dsn)),
        ),
    )
    monkeypatch.setattr(
        entry, "database_ready", lambda s, **kw: steps.append(("check", kw)) or True
    )
    entry.launch(configured, "initialize")
    assert [s[0] for s in steps] == ["wait", "initialize", "check"]
    assert steps[0][1] == {"require_tables": False}
    assert steps[-1][1] == {"require_tables": True}


def test_initialize_failure_is_sanitized(entry, configured, monkeypatch, capsys):
    monkeypatch.setattr(entry, "wait_dependencies", lambda *a, **kw: None)

    def fail():
        raise RuntimeError("postgresql://user:private-password@host/db")

    monkeypatch.setattr(
        entry, "HumanReviewRepository", lambda dsn: SimpleNamespace(initialize=fail)
    )
    assert entry.main(["initialize"]) == 1
    assert "private-password" not in capsys.readouterr().err


def test_api_exec_receives_signals_and_uses_container_bind_address(
    entry,
    configured,
    monkeypatch,
):
    commands = []
    monkeypatch.setattr(entry, "wait_dependencies", lambda *a, **kw: None)
    monkeypatch.setattr(entry.os, "chdir", lambda path: None)
    monkeypatch.setattr(entry.os, "execv", lambda executable, command: commands.append(command))
    entry.launch(configured, "api")
    assert commands == [
        [
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
    ]
    assert configured.asset_root.is_dir()


@pytest.fixture
def worker_fixture(entry, monkeypatch, tmp_path):
    script = tmp_path / "fake_worker.py"
    script.write_text("import time\ntime.sleep(60)\n")
    monkeypatch.setattr(entry, "WORKER", script)
    monkeypatch.setattr(entry, "ready_file", lambda role: tmp_path / f"{role}.json")
    process = subprocess.Popen(entry.worker_command("cpu"))
    marker = entry.ready_file("cpu")
    marker.write_text(json.dumps({"pid": process.pid, "kind": "cpu"}))
    try:
        yield process, marker
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_worker_health_checks_actual_process_identity_and_marker_age(entry, worker_fixture):
    process, marker = worker_fixture
    assert entry.worker_ready("cpu")
    marker.write_text(json.dumps({"pid": process.pid, "kind": "gpu"}))
    assert not entry.worker_ready("cpu")
    marker.write_text(json.dumps({"pid": os.getpid(), "kind": "cpu"}))
    assert not entry.worker_ready("cpu")
    marker.write_text(json.dumps({"pid": process.pid, "kind": "cpu"}))
    os.utime(marker, (1, 1))
    assert not entry.worker_ready("cpu")


def test_stale_marker_cleanup_never_removes_live_marker(entry, worker_fixture):
    _, marker = worker_fixture
    with pytest.raises(entry.ContainerError, match="already running"):
        entry.clear_stale_marker("cpu")
    assert marker.exists()
    marker.write_text("invalid stale marker")
    entry.clear_stale_marker("cpu")
    assert not marker.exists()


def test_ready_marker_symlink_is_never_followed_or_deleted(entry, monkeypatch, tmp_path):
    target = tmp_path / "keep.json"
    target.write_text("keep")
    marker = tmp_path / "ready.json"
    marker.symlink_to(target)
    monkeypatch.setattr(entry, "ready_file", lambda role: marker)
    assert not entry.worker_ready("cpu")
    with pytest.raises(entry.ContainerError, match="symlink"):
        entry.clear_stale_marker("cpu")
    assert target.read_text() == "keep"
    assert marker.is_symlink()


def test_health_requires_dependency_readiness_not_just_alive_process(
    entry, configured, monkeypatch
):
    monkeypatch.setattr(entry, "api_ready", lambda: True)
    monkeypatch.setattr(entry, "database_ready", lambda *a, **kw: True)
    monkeypatch.setattr(entry, "temporal_ready", lambda s: False)
    assert not entry.healthcheck(configured, "api")
    monkeypatch.setattr(entry, "temporal_ready", lambda s: True)
    assert entry.healthcheck(configured, "api")


def test_database_failure_does_not_expose_dsn(entry, configured, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("secret from connection error")

    monkeypatch.setattr(entry.psycopg, "connect", fail)
    assert not entry.database_ready(configured, require_tables=True)


def test_check_does_not_load_models_or_start_services(entry, configured, monkeypatch):
    monkeypatch.setattr(entry, "validate_configuration", lambda *a: pytest.fail("loaded models"))
    monkeypatch.setattr(entry, "healthcheck", lambda s, role: role == "api")
    assert entry.main(["check", "api"]) == 0


def test_postgres_bootstrap_uses_quoted_password_variables_and_separate_roles(tmp_path):
    """Exercise shell argument construction without running psql or touching any DB."""
    psql = tmp_path / "psql"
    captured = tmp_path / "captured.json"
    psql.write_text(
        f"#!{sys.executable}\nimport json, os, sys\n"
        "from pathlib import Path\n"
        "Path(os.environ['CAPTURE']).write_text(json.dumps({'argv': sys.argv[1:], "
        "'sql': sys.stdin.read()}))\n"
    )
    psql.chmod(0o700)
    app_password = "quote' slash\\ whitespace $value"
    env = {
        **os.environ,
        "PATH": str(tmp_path) + os.pathsep + os.environ.get("PATH", ""),
        "CAPTURE": str(captured),
        "SEWER_APP_DB_PASSWORD": app_password,
        "SEWER_TEMPORAL_DB_PASSWORD": "other'password",
        "POSTGRES_USER": "postgres",
    }
    result = subprocess.run(
        ["sh", str(ENTRYPOINT.with_name("init-postgres.sh"))],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(captured.read_text())
    assert f"--set=app_password={app_password}" in payload["argv"]
    assert ":'app_password'" in payload["sql"]
    assert "PASSWORD %L" in payload["sql"]
    assert "NOSUPERUSER NOCREATEDB NOCREATEROLE" in payload["sql"]
    assert "WHERE NOT EXISTS" in payload["sql"]
    assert "REVOKE ALL ON DATABASE temporal FROM PUBLIC" in payload["sql"]
    assert app_password not in result.stdout + result.stderr
