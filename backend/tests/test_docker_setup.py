import json
import stat
import subprocess

import pytest

from scripts import docker_setup
from scripts.docker_setup import PASSWORD_FIELDS, create_configuration


def test_docker_configuration_is_private_and_independent(tmp_path):
    native = tmp_path / ".env"
    native.write_text("EXISTING_SECRET=unchanged\n")
    output = tmp_path / ".env.docker"
    models = tmp_path / "model bundle"
    create_configuration(output, models)
    text = output.read_text()
    values = dict(
        line.split("=", 1) for line in text.splitlines() if line and not line.startswith("#")
    )
    passwords = [values[field] for field in PASSWORD_FIELDS]
    assert len(set(passwords)) == 3
    assert all(len(password) == 48 for password in passwords)
    assert values["DASHSCOPE_API_KEY"] == ""
    assert values["SEWER_MODELS_DIR"] == f"'{models}'"
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert native.read_text() == "EXISTING_SECRET=unchanged\n"


def test_rerun_preserves_existing_credentials(tmp_path):
    output = tmp_path / ".env.docker"
    create_configuration(output)
    before = output.read_bytes()
    with pytest.raises(FileExistsError):
        create_configuration(output)
    assert output.read_bytes() == before


def test_rejects_compose_interpolation_in_model_path(tmp_path):
    output = tmp_path / ".env.docker"
    with pytest.raises(ValueError):
        create_configuration(output, tmp_path / "$UNEXPECTED")
    assert not output.exists()


@pytest.fixture
def compose(tmp_path, monkeypatch):
    # Let Compose represent environment interpolation and mount resolution. All real local
    # writes go to the isolated deployment config; record every command before Docker.
    template = docker_setup.REPO_ROOT / ".env.docker.example"
    (tmp_path / template.name).write_bytes(template.read_bytes())
    monkeypatch.setattr(docker_setup, "REPO_ROOT", tmp_path)
    settings = {
        "SEWER_LLM_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "SEWER_LLM_MODEL": "qwen3.8-max",
        "SEWER_VLM_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "SEWER_VLM_MODEL": "qwen3.8-max",
    }
    output = tmp_path / ".env.docker"
    models = tmp_path / "my models"
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs["env"]))
        if "config" in command:
            line = next(
                v for v in output.read_text().splitlines() if v.startswith("DASHSCOPE_API_KEY=")
            )
            env = {**settings, "DASHSCOPE_API_KEY": line.partition("=")[2]}
            service = {
                "environment": env,
                "volumes": [
                    {"type": "bind", "target": "/models", "source": str(models)},
                ],
            }
            return subprocess.CompletedProcess(
                command, 0, json.dumps({"services": {"api": service}})
            )
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", run)
    return output, models, settings, calls


def test_one_command_first_launch_and_repeat_preserve_key_and_passwords(compose, monkeypatch):
    output, models, _, calls = compose
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-original")
    monkeypatch.setenv("HF_TOKEN", "hf-private")
    monkeypatch.setenv("SEWER_LLM_API_KEY", "role-secret")
    docker_setup.deploy(output, models, [])
    original = output.read_bytes()
    assert b"DASHSCOPE_API_KEY=sk-original\n" in original
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    fetch, build, up = calls[-3:]
    assert fetch[0][-3:] == ["fetch-public", "--directory", str(models)]
    assert {"DASHSCOPE_API_KEY", "HF_TOKEN", "SEWER_LLM_API_KEY"}.isdisjoint(fetch[1])
    assert build[0][-2:] == ["build", "initialize"]
    assert up[0][-6:] == ["up", "-d", "--no-build", "--wait", "--wait-timeout", "360"]
    assert "sk-original" not in str([c[0] for c in calls])

    monkeypatch.setenv("DASHSCOPE_API_KEY", "different-account")
    docker_setup.deploy(output, None, ["api", "cpu-worker"])
    assert output.read_bytes() == original
    assert "DASHSCOPE_API_KEY" not in calls[-1][1]
    assert calls[-1][0][-2:] == ["api", "cpu-worker"]


def test_setup_without_key_opens_the_workbench_without_prompting(compose, monkeypatch):
    output, _, _, _ = compose
    create_configuration(output)
    before = output.read_bytes()
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.setattr(docker_setup.sys.stdin, "isatty", lambda: True)
    docker_setup.deploy(output, None, [])
    assert output.read_bytes() == before


def test_explicit_provider_roles_need_no_shared_key_or_edit(compose, monkeypatch):
    output, _, settings, _ = compose
    create_configuration(output)
    before = output.read_bytes()
    for role in ("LLM", "VLM"):
        settings.update(
            {
                f"SEWER_{role}_BASE_URL": "https://example.test/v1",
                f"SEWER_{role}_MODEL": role,
                f"SEWER_{role}_API_KEY": "role-key",
            }
        )
    monkeypatch.setenv("DASHSCOPE_API_KEY", "unrelated-shared-key")
    docker_setup.deploy(output, None, [])
    assert output.read_bytes() == before


def test_missing_custom_provider_key_can_be_configured_in_the_browser(compose, monkeypatch):
    output, _, settings, calls = compose
    settings["SEWER_VLM_BASE_URL"] = "https://example.test/v1"
    monkeypatch.setenv("DASHSCOPE_API_KEY", "unrelated-shared-key")
    docker_setup.deploy(output, None, [])
    assert any("fetch-public" in command for command, _ in calls)


def test_failed_download_never_builds_or_starts(compose, monkeypatch):
    output, _, _, calls = compose
    run = subprocess.run

    def interrupted(command, **kwargs):
        if "fetch-public" in command:
            raise subprocess.CalledProcessError(1, command)
        return run(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", interrupted)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-preserve")
    with pytest.raises(subprocess.CalledProcessError):
        docker_setup.deploy(output, None, [])
    assert "DASHSCOPE_API_KEY=sk-preserve" in output.read_text()
    assert not any("up" in command or "build" in command for command, _ in calls)
