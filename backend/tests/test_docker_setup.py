import stat

import pytest

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
