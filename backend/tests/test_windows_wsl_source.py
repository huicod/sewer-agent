"""Offline tests for source transfer and configuration preservation; no system installation."""

import importlib.util
import io
import json
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("wsl_source", ROOT / "scripts/wsl_source.py")
source = importlib.util.module_from_spec(spec)
spec.loader.exec_module(source)


def bundle(tmp_path, overrides=None):
    files = {
        "compose.yaml": b"name: test\n",
        "Dockerfile": b"FROM test\n",
        "scripts/docker_setup.py": b"# setup\n",
        "pyproject.toml": b"[project]\n",
        "scripts/example.sh": b"#!/bin/bash\r\necho ok\r\n",
    }
    files.update(overrides or {})
    archive = tmp_path / "source.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        for name, data in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            output.addfile(member, io.BytesIO(data))
    return archive


def test_install_and_identical_retry_preserve_runtime_data(tmp_path):
    archive = bundle(tmp_path)
    destination = tmp_path / "app"
    assert source.install_source(archive, destination) == (5, True)
    assert (destination / "scripts/example.sh").read_bytes() == b"#!/bin/bash\necho ok\n"
    (destination / ".env.docker").write_text("PRIVATE=value\n")
    (destination / "models").mkdir()
    (destination / "models/model.pt").write_bytes(b"weights")
    with patch.object(
        source, "running_project", side_effect=AssertionError("No runtime query on identical retry")
    ):
        assert source.install_source(archive, destination) == (5, False)
    assert (destination / ".env.docker").read_text() == "PRIVATE=value\n"
    assert (destination / "models/model.pt").read_bytes() == b"weights"


def test_changed_source_requires_stopped_project_and_updates_after_stop(tmp_path):
    destination = tmp_path / "app"
    source.install_source(bundle(tmp_path), destination)
    changed = bundle(tmp_path, {"Dockerfile": b"FROM new\n"})
    with (
        patch.object(source, "running_project", return_value=True),
        pytest.raises(ValueError, match="running"),
    ):
        source.install_source(changed, destination)
    assert (destination / "Dockerfile").read_bytes() == b"FROM test\n"
    with patch.object(source, "running_project", return_value=False):
        assert source.install_source(changed, destination)[1]
    assert (destination / "Dockerfile").read_bytes() == b"FROM new\n"


def test_existing_unmanaged_directory_is_not_overwritten(tmp_path):
    destination = tmp_path / "app"
    destination.mkdir()
    (destination / "note.txt").write_text("keep")
    with pytest.raises(ValueError, match="not managed"):
        source.install_source(bundle(tmp_path), destination)
    assert (destination / "note.txt").read_text() == "keep"


@pytest.mark.parametrize(
    "name",
    [
        "../outside",
        "/tmp/outside",
        ".env",
        "backend/.env",
        "frontend/node_modules/a",
        "models/checkpoint.pt",
        "var/data",
    ],
)
def test_disallowed_archive_members_are_rejected_before_install(tmp_path, name):
    destination = tmp_path / "app"
    with pytest.raises(ValueError, match="unexpected"):
        source.install_source(bundle(tmp_path, {name: b"bad"}), destination)
    assert not destination.exists()


def test_archive_symlink_is_rejected(tmp_path):
    archive = bundle(tmp_path)
    # A standalone link archive is enough to exercise the type check before completeness checks.
    with tarfile.open(archive, "w:gz") as output:
        member = tarfile.TarInfo("scripts/link")
        member.type = tarfile.SYMTYPE
        member.linkname = "/etc/passwd"
        output.addfile(member)
    with pytest.raises(ValueError, match="unexpected"):
        source.install_source(archive, tmp_path / "app")


def test_managed_symlink_cannot_redirect_update(tmp_path):
    destination = tmp_path / "app"
    source.install_source(bundle(tmp_path), destination)
    outside = tmp_path / "outside"
    outside.write_text("keep")
    (destination / "Dockerfile").unlink()
    (destination / "Dockerfile").symlink_to(outside)
    with pytest.raises(ValueError, match="escapes|symbolic"):
        source.install_source(bundle(tmp_path), destination)
    assert outside.read_text() == "keep"


def test_install_directory_must_be_beneath_user_home(tmp_path):
    assert source.installation_path("app", tmp_path) == tmp_path / "app"
    for path in (str(tmp_path), "/", "../other"):
        with pytest.raises(ValueError, match="dedicated"):
            source.installation_path(path, tmp_path)


def test_existing_configuration_is_never_recreated(tmp_path):
    config = tmp_path / ".env.docker"
    original = b"DATABASE_PASSWORD=secret\r\nSEWER_HTTP_PORT=18082\r\n"
    config.write_bytes(original)
    with patch.object(source.subprocess, "run", side_effect=AssertionError("must preserve")):
        source.initialize_environment(tmp_path, 8000, "different-project")
    assert config.read_bytes() == original


def test_first_config_uses_separate_project_image_and_port(tmp_path):
    config = tmp_path / ".env.docker"

    def generate(*args, **kwargs):
        Path(args[0][-1]).write_text(
            "COMPOSE_PROJECT_NAME=sewer-agent\nSEWER_IMAGE_TAG=local\nSEWER_HTTP_PORT=8000\nPASSWORD=keep\n"
        )

    with patch.object(source.subprocess, "run", side_effect=generate):
        source.initialize_environment(tmp_path, 18099, "sewer-test")
    assert (
        config.read_text() == "COMPOSE_PROJECT_NAME=sewer-test\nSEWER_IMAGE_TAG=sewer-test\n"
        "SEWER_HTTP_PORT=18099\nPASSWORD=keep\n"
    )
    assert config.stat().st_mode & 0o777 == 0o600


def test_systemd_update_preserves_other_sections_and_is_repeatable():
    original = (
        "# keep\n[boot]\ncommand=echo hello\nsystemd=false\n[network]\ngenerateResolvConf=false\n"
    )
    updated = source.with_systemd_enabled(original)
    assert updated == original.replace("systemd=false", "systemd=true")
    assert source.with_systemd_enabled(updated) == updated
    added = source.with_systemd_enabled("[network]\ngenerateResolvConf=false\n")
    assert "generateResolvConf=false" in added and "[boot]\nsystemd=true" in added
    assert source.with_systemd_enabled("[boot]\ncommand=echo ok") == (
        "[boot]\ncommand=echo ok\nsystemd=true\n"
    )


def test_deleted_managed_file_only_is_removed_on_update(tmp_path):
    destination = tmp_path / "app"
    source.install_source(bundle(tmp_path, {"scripts/retired.py": b"old"}), destination)
    (destination / "personal.txt").write_text("keep")
    with patch.object(source, "running_project", return_value=False):
        source.install_source(bundle(tmp_path), destination)
    assert not (destination / "scripts/retired.py").exists()
    assert (destination / "personal.txt").read_text() == "keep"
    assert (
        "scripts/retired.py" not in json.loads((destination / source.MARKER).read_text())["files"]
    )
