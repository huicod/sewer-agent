"""Mock the operating-system commands: these tests never install or restart services."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="WSL launcher runs on Linux")


@pytest.fixture
def launcher(tmp_path):
    home = tmp_path / "home"
    app = home / "app"
    app.mkdir(parents=True)
    (app / ".sewer-wsl-install.json").write_text(json.dumps({"version": 1, "files": []}))
    (app / ".env.docker").write_text("PRIVATE=unchanged\n")
    commands = tmp_path / "commands"
    commands.mkdir()
    log = tmp_path / "calls"
    scripts = {
        "id": "#!/bin/bash\nif [[ $1 == -u ]]; then echo 1000; else echo tester; fi\n",
        "sudo": """#!/bin/bash
printf 'sudo %s\n' "$*" >> "$CALLS"
[[ $1 != -v ]] || exit 0
[[ $* != *"wsl_source.py systemd"* ]] || exit 0
while [[ $1 == -u || $1 == -g ]]; do shift 2; done
[[ $1 != -- ]] || shift
exec "$@"
""",
        "systemctl": '#!/bin/bash\nprintf "systemctl %s\\n" "$*" >> "$CALLS"\n',
        "docker": """#!/bin/bash
printf 'docker %s\n' "$*" >> "$CALLS"
if [[ $* == *"config --format json"* ]]; then
  echo '{"name":"test-project","services":{"api":{"ports":[{"published":"18099"}]}}}'
elif [[ $1 == ps ]]; then
  printf '%s\n' "${FOREIGN_PROJECT:-}"
elif [[ $* == *" up "* && ${FAIL_START:-0} == 1 ]]; then
  exit 77
fi
""",
        "ps": "#!/bin/bash\necho init\n",
    }
    for name, script in scripts.items():
        path = commands / name
        path.write_text(script)
        path.chmod(0o755)
    environment = dict(os.environ, HOME=str(home), CALLS=str(log))
    environment["PATH"] = str(commands) + os.pathsep + environment["PATH"]

    def run(action, extra=None):
        return subprocess.run(
            [
                "bash",
                str(ROOT / "scripts/wsl_setup.sh"),
                "--action",
                action,
                "--directory",
                "app",
                *(extra or []),
            ],
            env=environment,
            capture_output=True,
            text=True,
        )

    return run, environment, app, log


def test_status_is_read_only_and_uses_saved_port(launcher):
    run, _, app, log = launcher
    result = run("status")
    assert result.returncode == 0, result.stderr
    assert "127.0.0.1:18099" in result.stdout
    calls = log.read_text()
    assert "compose --env-file .env.docker ps -a" in calls
    assert "systemctl" not in calls and " up " not in calls
    assert not (app / ".wsl-control").exists()


def test_stop_retains_volumes_and_clears_only_keepalive_marker(launcher):
    run, _, app, log = launcher
    control = app / ".wsl-control"
    control.mkdir()
    (control / "running").touch()
    result = run("stop")
    assert result.returncode == 0, result.stderr
    assert "compose --env-file .env.docker stop" in log.read_text()
    assert " down" not in log.read_text()
    assert not (control / "running").exists()
    assert (app / ".env.docker").read_text() == "PRIVATE=unchanged\n"


def test_start_uses_existing_images_and_registers_keepalive(launcher):
    run, _, app, log = launcher
    result = run("start")
    assert result.returncode == 0, result.stderr
    assert "--no-build --pull never --wait" in log.read_text()
    assert (app / ".wsl-control/running").is_file()


def test_failed_start_propagates_and_does_not_claim_ready(launcher):
    run, environment, app, _ = launcher
    environment["FAIL_START"] = "1"
    result = run("start")
    assert result.returncode == 77
    assert "Open http" not in result.stdout
    assert not (app / ".wsl-control/running").exists()


def test_foreign_compose_project_is_not_started_or_stopped(launcher):
    run, environment, _, log = launcher
    environment["FOREIGN_PROJECT"] = "/home/someone/another-app"
    result = run("stop")
    assert result.returncode == 1
    assert "belongs to another directory" in result.stderr
    assert "compose --env-file .env.docker stop" not in log.read_text()


def test_systemd_stage_returns_resumable_code_without_installation(launcher, tmp_path):
    if "ID=ubuntu" not in Path("/etc/os-release").read_text():
        pytest.skip("WSL setup preflight requires Ubuntu")
    run, _, app, log = launcher
    archive = tmp_path / "source.tar.gz"
    archive.touch()
    result = run("setup", ["--archive", str(archive)])
    assert result.returncode == 20, result.stderr
    assert "Systemd enabled" in result.stdout
    assert "apt-get" not in log.read_text()
    assert "docker" not in log.read_text()
    assert (app / ".env.docker").read_text() == "PRIVATE=unchanged\n"


def test_root_docker_pins_local_socket_after_sudo_changes_environment(tmp_path):
    commands = tmp_path / "commands"
    commands.mkdir()
    sudo = commands / "sudo"
    sudo.write_text(
        '#!/bin/bash\nunset DOCKER_HOST\nexport DOCKER_CONTEXT=root-remote\nexec "$@"\n'
    )
    docker = commands / "docker"
    docker.write_text(
        "#!/bin/bash\n"
        "[[ -z ${DOCKER_CONTEXT:-} ]] || exit 81\n"
        "[[ $1 == --host && $2 == unix:///var/run/docker.sock ]] || exit 82\n"
        "shift 2\n"
        'printf "%s\\n" "$*" >> "$CALLS"\n'
    )
    sudo.chmod(0o755)
    docker.chmod(0o755)
    source = (ROOT / "scripts/wsl_setup.sh").read_text()
    definitions = source.split("\naction=setup\n", 1)[0]
    # Exercise the actual privileged Docker boundary without the installer's system mutations.
    operations = [
        "compose version",
        "info --format '{{.OperatingSystem}}'",
        "info --format '{{json .Runtimes}}'",
        "ps -q",
        "run --rm --gpus all ubuntu:24.04 nvidia-smi -L",
    ]
    result = subprocess.run(
        ["bash", "-c", definitions + "\n" + "\n".join("root_docker " + op for op in operations)],
        env=dict(
            os.environ,
            PATH=str(commands) + os.pathsep + os.environ["PATH"],
            DOCKER_HOST="unix:///var/run/docker.sock",
            CALLS=str(tmp_path / "calls"),
        ),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert len((tmp_path / "calls").read_text().splitlines()) == len(operations)
    assert "sudo docker " not in source
