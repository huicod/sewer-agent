"""Small safety and evidence helpers for the disruptive business recovery test."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path


def load_restart_output(environment: Mapping[str, str], *, repo_root: Path) -> Path:
    if not all(
        environment.get(name) == "1"
        for name in ("SEWER_RUN_RESTART_TEST", "SEWER_RUN_BUSINESS_RESTART_TEST")
    ):
        raise RuntimeError("business recovery requires both explicit restart gates")
    configured = Path(environment.get("SEWER_BUSINESS_RESTART_OUTPUT", ""))
    base = (repo_root / "var" / "evaluations").resolve()
    output = configured.resolve()
    if (
        not configured.is_absolute()
        or output == base
        or not output.is_relative_to(base)
        or output.exists()
    ):
        raise RuntimeError(
            "SEWER_BUSINESS_RESTART_OUTPUT must be a new absolute directory strictly under "
            "var/evaluations"
        )
    return output


def assert_app_stopped(status: Mapping[str, object]) -> None:
    components = status.get("components")
    if not isinstance(components, Mapping) or any(
        not isinstance(components.get(role), Mapping)
        or components[role].get("running") is not False
        for role in ("api", "cpu", "gpu")
    ):
        raise RuntimeError("managed application must be stopped before business recovery")


def assert_workflow_scope(running_ids: list[str], owned_ids: set[str]) -> None:
    running = set(running_ids)
    if running - owned_ids:
        raise RuntimeError("foreign running workflow blocks business recovery")
    if owned_ids - running:
        raise RuntimeError("owned running workflow set changed before restart")
