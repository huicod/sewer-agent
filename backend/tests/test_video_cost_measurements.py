from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from video_cost_live import (
    load_video_cost_admission,
    require_bounded_api_output,
    summarize_measurements,
)


def _probe(*, duration_seconds: float = 22.4) -> dict[str, int | float]:
    return {"duration_seconds": duration_seconds, "width": 720, "height": 576}


def test_admission_requires_both_paid_gates_and_explicit_safe_paths(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fixture")
    evaluations = tmp_path / "var" / "evaluations"
    evaluations.mkdir(parents=True)
    output = evaluations / "run-one"

    assert load_video_cost_admission({}, repo_root=tmp_path) is None
    assert (
        load_video_cost_admission(
            {"SEWER_RUN_VIDEO_COST_LIVE": "0", "SEWER_RUN_LIVE_API": "0"},
            repo_root=tmp_path,
        )
        is None
    )
    for values, message in (
        (
            {"SEWER_RUN_VIDEO_COST_LIVE": "1"},
            "both explicit live API gates",
        ),
        (
            {
                "SEWER_RUN_VIDEO_COST_LIVE": "1",
                "SEWER_RUN_LIVE_API": "1",
                "SEWER_LIVE_VIDEO": "relative.mp4",
                "SEWER_VIDEO_COST_OUTPUT": str(output),
            },
            "existing absolute SEWER_LIVE_VIDEO",
        ),
        (
            {
                "SEWER_RUN_VIDEO_COST_LIVE": "1",
                "SEWER_RUN_LIVE_API": "1",
                "SEWER_LIVE_VIDEO": str(source),
                "SEWER_VIDEO_COST_OUTPUT": str(tmp_path / "outside"),
            },
            "strictly under",
        ),
    ):
        with pytest.raises(RuntimeError, match=message):
            load_video_cost_admission(values, repo_root=tmp_path, probe=lambda _: _probe())

    admission = load_video_cost_admission(
        {
            "SEWER_RUN_VIDEO_COST_LIVE": "1",
            "SEWER_RUN_LIVE_API": "1",
            "SEWER_LIVE_VIDEO": str(source),
            "SEWER_VIDEO_COST_OUTPUT": str(output),
        },
        repo_root=tmp_path,
        probe=lambda _: _probe(),
    )
    assert admission is not None
    assert admission.source == source
    assert admission.output == output
    assert admission.duration_seconds == 22.4
    assert admission.width == 720
    assert admission.height == 576


def test_admission_rejects_existing_output_and_video_over_sixty_seconds(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fixture")
    evaluations = tmp_path / "var" / "evaluations"
    evaluations.mkdir(parents=True)
    output = evaluations / "run-one"
    values = {
        "SEWER_RUN_VIDEO_COST_LIVE": "1",
        "SEWER_RUN_LIVE_API": "1",
        "SEWER_LIVE_VIDEO": str(source),
        "SEWER_VIDEO_COST_OUTPUT": str(output),
    }

    with pytest.raises(RuntimeError, match="at most 60 seconds"):
        load_video_cost_admission(
            values,
            repo_root=tmp_path,
            probe=lambda _: _probe(duration_seconds=60.01),
        )

    output.mkdir()
    with pytest.raises(RuntimeError, match="new SEWER_VIDEO_COST_OUTPUT"):
        load_video_cost_admission(
            values,
            repo_root=tmp_path,
            probe=lambda _: _probe(),
        )


def test_preflight_rejects_missing_api_output_bound_and_preserves_configured_value() -> None:
    with pytest.raises(RuntimeError, match="explicit SEWER_API_MAX_OUTPUT_TOKENS"):
        require_bounded_api_output(SimpleNamespace(api_max_output_tokens=None))

    configured = SimpleNamespace(api_max_output_tokens=4096)
    assert require_bounded_api_output(configured) is configured
    assert configured.api_max_output_tokens == 4096


def test_summary_sums_provider_returned_tokens() -> None:
    result = summarize_measurements(
        provider={
            "api_request_limit": 8,
            "provider_calls": [
                {
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 2,
                        "total_tokens": 12,
                        "cached_tokens": 3,
                    }
                },
                {
                    "usage": {
                        "prompt_tokens": 7,
                        "completion_tokens": 1,
                        "total_tokens": 8,
                    }
                },
            ],
            "budget_rejections": 0,
        },
        activity={"calls": []},
        processing_elapsed_seconds=9.0,
    )

    assert result["provider_usage"] == {
        "physical_request_count": 2,
        "api_request_limit": 8,
        "budget_rejections": 0,
        "calls_with_usage": 2,
        "calls_missing_usage": 0,
        "known_token_sums": {
            "prompt_tokens": 17,
            "completion_tokens": 3,
            "total_tokens": 20,
            "cached_tokens": 3,
        },
        "token_totals_complete": True,
    }


def test_summary_keeps_missing_usage_and_missing_token_fields_explicit() -> None:
    result = summarize_measurements(
        provider={
            "api_request_limit": 8,
            "provider_calls": [
                {"usage": {"prompt_tokens": 5}},
                {"usage": None},
            ],
            "budget_rejections": 1,
        },
        activity={"calls": []},
        processing_elapsed_seconds=4.0,
    )

    usage = result["provider_usage"]
    assert usage["physical_request_count"] == 2
    assert usage["calls_with_usage"] == 1
    assert usage["calls_missing_usage"] == 1
    assert usage["known_token_sums"] == {
        "prompt_tokens": 5,
        "completion_tokens": None,
        "total_tokens": None,
        "cached_tokens": None,
    }
    assert usage["token_totals_complete"] is False


def test_summary_keeps_activity_stage_times_distinct_from_processing_elapsed() -> None:
    result = summarize_measurements(
        provider={
            "api_request_limit": 8,
            "provider_calls": [],
            "budget_rejections": 0,
        },
        activity={
            "calls": [
                {"activity": "scan_video", "status": "succeeded", "elapsed_seconds": 2.0},
                {
                    "activity": "prepare_video",
                    "status": "succeeded",
                    "elapsed_seconds": 0.5,
                },
                {
                    "activity": "verify_video_frame",
                    "status": "succeeded",
                    "elapsed_seconds": 1.0,
                },
            ]
        },
        processing_elapsed_seconds=9.0,
    )

    assert result["processing_elapsed_seconds"] == 9.0
    assert result["activity_seconds"] == {
        "scan_video": 2.0,
        "prepare_video": 0.5,
        "verify_video_frame": 1.0,
    }
    assert result["activity_attempts"] == {
        "scan_video": 1,
        "prepare_video": 1,
        "verify_video_frame": 1,
    }
    assert sum(result["activity_seconds"].values()) == 3.5
