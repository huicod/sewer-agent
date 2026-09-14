from pathlib import Path

import pytest
from business_restart_support import (
    assert_app_stopped,
    assert_workflow_scope,
    load_restart_output,
)


@pytest.mark.parametrize(
    "environment",
    [
        {},
        {"SEWER_RUN_RESTART_TEST": "1"},
        {"SEWER_RUN_BUSINESS_RESTART_TEST": "1"},
    ],
)
def test_restart_admission_requires_both_explicit_gates(environment, tmp_path):
    environment["SEWER_BUSINESS_RESTART_OUTPUT"] = str(tmp_path / "unused")

    with pytest.raises(RuntimeError, match="both explicit restart gates"):
        load_restart_output(environment, repo_root=tmp_path)


@pytest.mark.parametrize("kind", ["relative", "outside", "root", "existing"])
def test_restart_admission_requires_new_strict_evaluation_child(kind, tmp_path):
    evaluations = tmp_path / "var" / "evaluations"
    evaluations.mkdir(parents=True)
    output = {
        "relative": Path("var/evaluations/run"),
        "outside": tmp_path / "elsewhere" / "run",
        "root": evaluations,
        "existing": evaluations / "existing",
    }[kind]
    if kind == "existing":
        output.mkdir()
    environment = {
        "SEWER_RUN_RESTART_TEST": "1",
        "SEWER_RUN_BUSINESS_RESTART_TEST": "1",
        "SEWER_BUSINESS_RESTART_OUTPUT": str(output),
    }

    with pytest.raises(RuntimeError, match="new absolute directory strictly under"):
        load_restart_output(environment, repo_root=tmp_path)


def test_restart_admission_accepts_new_strict_evaluation_child(tmp_path):
    output = tmp_path / "var" / "evaluations" / "run-001"
    environment = {
        "SEWER_RUN_RESTART_TEST": "1",
        "SEWER_RUN_BUSINESS_RESTART_TEST": "1",
        "SEWER_BUSINESS_RESTART_OUTPUT": str(output),
    }

    assert load_restart_output(environment, repo_root=tmp_path) == output
    assert not output.exists()


def test_app_guard_rejects_any_managed_component_still_running():
    stopped = {
        "components": {
            "api": {"running": False},
            "cpu": {"running": False},
            "gpu": {"running": False},
        }
    }
    assert_app_stopped(stopped)

    stopped["components"]["cpu"]["running"] = True
    with pytest.raises(RuntimeError, match="managed application must be stopped"):
        assert_app_stopped(stopped)


def test_workflow_guard_rejects_foreign_or_missing_owned_workflows():
    assert_workflow_scope([], set())
    assert_workflow_scope(["inspection:image", "inspection:video"], {
        "inspection:image",
        "inspection:video",
    })

    with pytest.raises(RuntimeError, match="foreign running workflow"):
        assert_workflow_scope(["inspection:foreign"], set())
    with pytest.raises(RuntimeError, match="owned running workflow set changed"):
        assert_workflow_scope(["inspection:image"], {"inspection:image", "inspection:video"})
