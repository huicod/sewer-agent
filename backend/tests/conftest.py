"""Unit tests never share persisted API credentials with other tests or the workbench."""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_runtime_settings(request, tmp_path, monkeypatch):
    if request.path.relative_to(Path(__file__).parent).parts[0] != "integration":
        monkeypatch.setenv("SEWER_RUNTIME_SETTINGS_DIR", str(tmp_path / "runtime-settings"))
