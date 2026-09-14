"""Bounded admission and measurement helpers for the opt-in video cost run."""

from __future__ import annotations

import json
import math
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from pipe_inspector.media import MediaError, MediaStore
from pipe_inspector.workflow.video_activities import VideoActivities
from temporalio import activity

REPO_ROOT = Path(__file__).resolve().parents[2]
MAX_VIDEO_SECONDS = 60.0
MAX_LONG_VIDEO_SECONDS = 900.0
_TOKEN_FIELDS = (
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cached_tokens",
)
_Settings = TypeVar("_Settings")


@dataclass(frozen=True, slots=True)
class VideoCostAdmission:
    source: Path
    output: Path
    duration_seconds: float
    width: int
    height: int


def require_bounded_api_output(settings: _Settings) -> _Settings:
    """Require the live harness to use a Settings-validated response token cap."""

    if getattr(settings, "api_max_output_tokens", None) is None:
        raise RuntimeError(
            "video cost acceptance requires explicit SEWER_API_MAX_OUTPUT_TOKENS"
        )
    return settings


def _probe_video(source: Path) -> dict[str, int | float]:
    try:
        metadata = MediaStore(source.parent)._inspect_video(source)
    except MediaError:
        raise RuntimeError("video cost admission requires a readable local video") from None
    return {
        "duration_seconds": metadata["duration_seconds"],
        "width": metadata["width"],
        "height": metadata["height"],
    }


def load_video_cost_admission(
    environ: Mapping[str, str],
    *,
    repo_root: Path = REPO_ROOT,
    probe: Callable[[Path], Mapping[str, int | float]] | None = None,
) -> VideoCostAdmission | None:
    """Admit one new local output only when both paid-run gates are exact."""

    video_gate = environ.get("SEWER_RUN_VIDEO_COST_LIVE")
    api_gate = environ.get("SEWER_RUN_LIVE_API")
    if video_gate in {None, "0"} and api_gate in {None, "0"}:
        return None
    if video_gate != "1" or api_gate != "1":
        raise RuntimeError("video cost run requires both explicit live API gates")
    long_video_gate = environ.get("SEWER_RUN_LONG_VIDEO_LIVE", "0")
    if long_video_gate not in {"0", "1"}:
        raise RuntimeError("SEWER_RUN_LONG_VIDEO_LIVE must be exactly 0 or 1")
    max_seconds = MAX_LONG_VIDEO_SECONDS if long_video_gate == "1" else MAX_VIDEO_SECONDS

    raw_source = environ.get("SEWER_LIVE_VIDEO", "")
    source = Path(raw_source)
    if not raw_source or not source.is_absolute() or not source.is_file():
        raise RuntimeError(
            "video cost run requires an existing absolute SEWER_LIVE_VIDEO"
        )
    source = source.resolve()

    raw_output = environ.get("SEWER_VIDEO_COST_OUTPUT", "")
    requested_output = Path(raw_output)
    if not raw_output or not requested_output.is_absolute():
        raise RuntimeError("SEWER_VIDEO_COST_OUTPUT must be an absolute new path")
    output = requested_output.resolve()
    evaluations = (repo_root.resolve() / "var" / "evaluations").resolve()
    if output == evaluations or not output.is_relative_to(evaluations):
        raise RuntimeError(
            "SEWER_VIDEO_COST_OUTPUT must be strictly under repo var/evaluations"
        )
    if output.exists():
        raise RuntimeError("video cost run requires a new SEWER_VIDEO_COST_OUTPUT")

    metadata = dict((probe or _probe_video)(source))
    try:
        duration = float(metadata["duration_seconds"])
        width, height = int(metadata["width"]), int(metadata["height"])
    except (KeyError, TypeError, ValueError):
        raise RuntimeError("video cost admission requires valid video metadata") from None
    if not math.isfinite(duration) or duration <= 0 or width <= 0 or height <= 0:
        raise RuntimeError("video cost admission requires valid video metadata")
    if duration > max_seconds:
        raise RuntimeError(f"video cost source must be at most {max_seconds:g} seconds")
    return VideoCostAdmission(
        source=source,
        output=output,
        duration_seconds=duration,
        width=width,
        height=height,
    )


class ActivityExecutionRecorder:
    """Record wall time per real activity invocation, including retries."""

    def __init__(self) -> None:
        self._calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def measure(self, name: str, function, *args, **kwargs):
        started = time.perf_counter()
        status = "succeeded"
        try:
            return function(*args, **kwargs)
        except BaseException:
            status = "failed"
            raise
        finally:
            self._append(name, status, time.perf_counter() - started)

    async def measure_async(self, name: str, function, *args, **kwargs):
        started = time.perf_counter()
        status = "succeeded"
        try:
            return await function(*args, **kwargs)
        except BaseException:
            status = "failed"
            raise
        finally:
            self._append(name, status, time.perf_counter() - started)

    def _append(self, name: str, status: str, elapsed: float) -> None:
        with self._lock:
            self._calls.append(
                {
                    "activity": name,
                    "status": status,
                    "elapsed_seconds": elapsed,
                }
            )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"calls": json.loads(json.dumps(self._calls))}


class MeasuredVideoActivities(VideoActivities):
    """Run production video activity methods under their registered names."""

    def __init__(self, *args, timing: ActivityExecutionRecorder, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.timing = timing

    @activity.defn(name="scan_video")
    def scan_video(self, run: dict) -> dict:
        return self.timing.measure("scan_video", super().scan_video, run)

    @activity.defn(name="prepare_video")
    def prepare_video(self, run: dict) -> dict:
        return self.timing.measure("prepare_video", super().prepare_video, run)

    @activity.defn(name="verify_video_frame")
    async def verify_video_frame(self, payload: dict) -> dict:
        return await self.timing.measure_async(
            "verify_video_frame", super().verify_video_frame, payload
        )

    @activity.defn(name="finish_video")
    def finish_video(self, payload: dict) -> dict:
        return self.timing.measure("finish_video", super().finish_video, payload)


def summarize_measurements(
    *,
    provider: Mapping[str, Any],
    activity: Mapping[str, Any],
    processing_elapsed_seconds: float,
) -> dict[str, Any]:
    """Summarize measured facts without inventing absent provider usage."""

    calls = list(provider.get("provider_calls", ()))
    supplied: dict[str, list[int]] = {name: [] for name in _TOKEN_FIELDS}
    calls_with_usage = 0
    complete_core_usage = True
    for call in calls:
        usage = call.get("usage") if isinstance(call, Mapping) else None
        if not isinstance(usage, Mapping):
            complete_core_usage = False
            continue
        calls_with_usage += 1
        for name in _TOKEN_FIELDS:
            value = usage.get(name)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                supplied[name].append(value)
        if any(name not in usage for name in _TOKEN_FIELDS[:3]):
            complete_core_usage = False

    activity_seconds: dict[str, float] = {}
    activity_attempts: dict[str, int] = {}
    for item in activity.get("calls", ()):
        name = str(item["activity"])
        activity_seconds[name] = activity_seconds.get(name, 0.0) + float(
            item["elapsed_seconds"]
        )
        activity_attempts[name] = activity_attempts.get(name, 0) + 1

    return {
        "processing_elapsed_seconds": processing_elapsed_seconds,
        "activity_seconds": activity_seconds,
        "activity_attempts": activity_attempts,
        "provider_usage": {
            "physical_request_count": len(calls),
            "api_request_limit": provider.get("api_request_limit"),
            "budget_rejections": provider.get("budget_rejections", 0),
            "calls_with_usage": calls_with_usage,
            "calls_missing_usage": len(calls) - calls_with_usage,
            "known_token_sums": {
                name: sum(values) if values else None for name, values in supplied.items()
            },
            "token_totals_complete": complete_core_usage,
        },
    }
