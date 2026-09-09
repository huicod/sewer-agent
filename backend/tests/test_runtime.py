from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

RUNTIME_PATH = Path(__file__).parents[2] / "scripts" / "runtime.py"


@pytest.fixture
def runtime():
    if not RUNTIME_PATH.exists():
        pytest.fail("scripts/runtime.py is not implemented")
    spec = importlib.util.spec_from_file_location("sewer_runtime", RUNTIME_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_rendered_temporal_config_is_loopback_only_and_has_no_optional_services(
    runtime, tmp_path: Path
) -> None:
    dynamic_config = (tmp_path / "dynamic.yaml").resolve()

    rendered = runtime.render_temporal_config("fixture-password", dynamic_config)

    assert rendered.count('connectAddr: "127.0.0.1:5432"') == 2
    assert rendered.count('bindOnIP: "127.0.0.1"') == 4
    assert 'broadcastAddress: "127.0.0.1"' in rendered
    assert f'filepath: "{dynamic_config}"' in rendered
    assert "numHistoryShards: 4" in rendered
    assert "archival:" not in rendered
    assert "metrics:" not in rendered
    assert "8000" not in rendered


def test_credentials_are_reused_and_env_content_is_preserved(runtime, tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    env_path = tmp_path / ".env"
    env_path.write_text("USER_SETTING=keep\n# user comment\n")
    os.chmod(env_path, 0o644)

    first = runtime.load_or_create_credentials(runtime_dir)
    runtime.write_managed_env(env_path, first)
    second = runtime.load_or_create_credentials(runtime_dir)
    runtime.write_managed_env(env_path, second)

    assert second == first
    assert first["SEWER_TEMPORAL_DB_PASSWORD"] != first["SEWER_APP_DB_PASSWORD"]
    assert len(first["SEWER_TEMPORAL_DB_PASSWORD"]) >= 32
    assert env_path.read_text().startswith("USER_SETTING=keep\n# user comment\n")
    assert env_path.read_text().count(runtime.ENV_BEGIN) == 1
    assert env_path.read_text().count(runtime.ENV_END) == 1
    assert (runtime_dir / "credentials.env").stat().st_mode & 0o777 == 0o600
    assert env_path.stat().st_mode & 0o777 == 0o600


def test_stop_refuses_pid_whose_command_does_not_match(runtime, tmp_path: Path) -> None:
    pid_file = tmp_path / "temporal.pid"
    config = tmp_path / "temporal.yaml"
    config.touch()
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    pid_file.write_text(f"{child.pid}\n")

    try:
        with pytest.raises(runtime.RuntimeControlError, match="identity"):
            runtime.stop_temporal(pid_file, Path(sys.executable), config, timeout=0.2)
        assert child.poll() is None
        assert pid_file.exists()
    finally:
        child.terminate()
        child.wait(timeout=5)


def test_stop_terminates_only_exact_controlled_process(runtime, tmp_path: Path) -> None:
    pid_file = tmp_path / "temporal.pid"
    config = (tmp_path / "temporal.yaml").resolve()
    config.touch()
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import time; time.sleep(30)",
            "--config-file",
            str(config),
        ]
    )
    pid_file.write_text(f"{child.pid}\n")

    assert runtime.process_matches(child.pid, Path(sys.executable), config)
    assert runtime.stop_temporal(pid_file, Path(sys.executable), config, timeout=2.0)
    assert child.poll() is not None
    assert not pid_file.exists()
