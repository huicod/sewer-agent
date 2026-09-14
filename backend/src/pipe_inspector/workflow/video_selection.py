"""Deterministic bounded candidate pooling and detailed-review selection."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from math import ceil
from pathlib import Path
from uuid import UUID, uuid5

from temporalio.exceptions import ApplicationError

from pipe_inspector.agent.api_client import APIClientError
from pipe_inspector.agent.image_review import review_required
from pipe_inspector.agent.video_selection import (
    SCENE_WALLCLOCK_SECONDS,
    VIDEO_SCENE_PROMPT_VERSION,
    VideoSceneSelector,
)
from pipe_inspector.contracts.assets import AssetRef
from pipe_inspector.contracts.tools import ToolCall, ToolError
from pipe_inspector.contracts.video import FrameObservation, VideoEvent, VideoScanResult
from pipe_inspector.contracts.video_selection import (
    VIDEO_SELECTION_VERSION,
    VideoReviewSelection,
    VideoSceneAssessment,
    VideoSceneBatch,
)

MAX_POOL_FRAMES = 64
AUDIT_FRACTION = 4


def _required_groups(
    scan: VideoScanResult,
    observations: dict[int, FrameObservation],
    *,
    force_review: bool,
) -> tuple[tuple[VideoEvent, tuple[int, ...]], ...]:
    groups = []
    for event in scan.state.events:
        frames = tuple(
            dict.fromkeys(
                item.frame_index
                for item in event.evidence
                if (observation := observations.get(item.frame_index)) is not None
                and (force_review or review_required(observation))
            )
        )
        if frames:
            groups.append((event, frames))
    return tuple(groups)


def _balanced_order(items: Sequence, *, timestamp) -> tuple:
    """Return a deterministic midpoint/endpoints-first order over the full timeline."""
    ordered = sorted(items, key=lambda item: (timestamp(item), repr(item)))
    if not ordered:
        return ()
    result, pending = [], [(0, len(ordered) - 1)]
    while pending:
        low, high = pending.pop(0)
        if low > high:
            continue
        middle = (low + high) // 2
        result.append(ordered[middle])
        # Queue both timeline halves so limited budgets cannot become an early prefix.
        pending.extend(((low, middle - 1), (middle + 1, high)))
    return tuple(result)


def _even_subset(items: Sequence[int], limit: int, *, timestamp) -> tuple[int, ...]:
    ordered = sorted(dict.fromkeys(items), key=lambda item: (timestamp(item), item))
    if limit <= 0 or not ordered:
        return ()
    if len(ordered) <= limit:
        return tuple(ordered)
    if limit == 1:
        return (ordered[len(ordered) // 2],)
    positions = [round(index * (len(ordered) - 1) / (limit - 1)) for index in range(limit)]
    return tuple(ordered[index] for index in positions)


def _frame_time(scan: VideoScanResult, observations: dict[int, FrameObservation], index: int):
    observation = observations.get(index)
    if observation is not None:
        return observation.timestamp_seconds
    for locator in scan.state.audit_frames:
        if locator.frame_index == index:
            return locator.timestamp_seconds
    return float(index)


def _add_groups(
    selected: set[int],
    groups: Iterable[tuple[VideoEvent, tuple[int, ...]]],
    capacity: int,
) -> None:
    for _, frames in groups:
        missing = set(frames).difference(selected)
        if missing and len(selected) + len(missing) <= capacity:
            selected.update(missing)


def _ordered_frames(
    scan: VideoScanResult,
    observations: dict[int, FrameObservation],
    frames: Iterable[int],
) -> tuple[int, ...]:
    return tuple(
        sorted(
            set(frames),
            key=lambda index: (_frame_time(scan, observations, index), index),
        )
    )


def build_candidate_pool(
    scan: VideoScanResult,
    observations: dict[int, FrameObservation],
    *,
    force_review: bool = False,
    budget: int = 32,
) -> tuple[int, ...]:
    """Build a time-balanced pool without splitting required event evidence groups."""
    if isinstance(budget, bool) or not isinstance(budget, int) or budget < 0:
        raise ValueError("budget must be a non-negative integer")
    capacity = min(MAX_POOL_FRAMES, budget * 2)
    if capacity == 0:
        return ()
    groups = _required_groups(scan, observations, force_review=force_review)
    group_order = _balanced_order(groups, timestamp=lambda item: item[0].start_seconds)
    audits = tuple(item.frame_index for item in scan.state.audit_frames)

    def time(index):
        return _frame_time(scan, observations, index)

    audit_reserve = min(len(set(audits)), ceil(capacity / AUDIT_FRACTION))
    event_target = max(0, capacity - audit_reserve)

    selected: set[int] = set()
    _add_groups(selected, group_order, event_target)
    selected.update(_even_subset(audits, audit_reserve, timestamp=time))
    _add_groups(selected, group_order, capacity)
    if len(selected) < capacity:
        remaining_audits = [index for index in audits if index not in selected]
        selected.update(
            _even_subset(remaining_audits, capacity - len(selected), timestamp=time)
        )
    return _ordered_frames(scan, observations, selected)


def select_video_review(
    scan: VideoScanResult,
    observations: dict[int, FrameObservation],
    assessments: Sequence[VideoSceneAssessment] = (),
    *,
    force_review: bool = False,
    budget: int = 32,
    scene_calls: int = 0,
    scene_errors: Sequence[str] = (),
) -> VideoReviewSelection:
    """Allocate the shared semantic budget; previews only affect priority."""
    if isinstance(budget, bool) or not isinstance(budget, int) or budget < 0:
        raise ValueError("budget must be a non-negative integer")
    if not 0 <= scene_calls <= min(4, budget):
        raise ValueError("scene calls must fit the shared semantic budget")
    assessed_indices = [item.frame_index for item in assessments]
    if len(assessed_indices) != len(set(assessed_indices)):
        raise ValueError("assessment frame indices must be unique")

    groups = _required_groups(scan, observations, force_review=force_review)
    audits = tuple(dict.fromkeys(item.frame_index for item in scan.state.audit_frames))
    candidate_frames = set(audits)
    for _, frames in groups:
        candidate_frames.update(frames)
    pool = build_candidate_pool(
        scan, observations, force_review=force_review, budget=budget
    )
    pool_set = set(pool)
    if not set(assessed_indices).issubset(pool_set):
        raise ValueError("assessments must belong to the candidate pool")
    assessment_by_frame = {item.frame_index: item for item in assessments}
    review_capacity = budget - scene_calls
    pooled_audits = tuple(index for index in audits if index in pool_set)
    audit_reserve = min(len(pooled_audits), ceil(review_capacity / AUDIT_FRACTION))
    event_target = max(0, review_capacity - audit_reserve)

    pooled_groups = [item for item in groups if set(item[1]).issubset(pool_set)]

    def deferred(item: tuple[VideoEvent, tuple[int, ...]]) -> bool:
        if force_review:
            return False
        scenes = [assessment_by_frame.get(index) for index in item[1]]
        return bool(scenes) and all(
            scene is not None and scene.scene in {"outside", "unassessable"}
            for scene in scenes
        )

    def priority(item: tuple[VideoEvent, tuple[int, ...]]) -> tuple[int, int]:
        scenes = [assessment_by_frame.get(index) for index in item[1]]
        has_pipe = any(scene is not None and scene.scene == "pipe" for scene in scenes)
        adequate = all(
            observations[index].quality.adequate
            for index in item[1]
            if index in observations
        )
        return (0 if has_pipe else 1, 0 if adequate else 1)

    eligible = [item for item in pooled_groups if not deferred(item)]
    delayed = [item for item in pooled_groups if deferred(item)]
    eligible_order = tuple(
        item
        for rank in sorted({priority(item) for item in eligible})
        for item in _balanced_order(
            [candidate for candidate in eligible if priority(candidate) == rank],
            timestamp=lambda candidate: candidate[0].start_seconds,
        )
    )
    delayed_order = _balanced_order(delayed, timestamp=lambda item: item[0].start_seconds)

    def time(index):
        return _frame_time(scan, observations, index)

    selected: set[int] = set()
    _add_groups(selected, eligible_order, event_target)
    selected.update(_even_subset(pooled_audits, audit_reserve, timestamp=time))
    _add_groups(selected, eligible_order, review_capacity)
    if len(selected) < review_capacity:
        remaining_audits = [index for index in pooled_audits if index not in selected]
        selected.update(
            _even_subset(
                remaining_audits,
                review_capacity - len(selected),
                timestamp=time,
            )
        )
    _add_groups(selected, delayed_order, review_capacity)
    selected.intersection_update(pool_set)
    review_frames = _ordered_frames(scan, observations, selected)
    unreviewed_events = sum(
        not set(frames).issubset(selected) for _, frames in groups
    )
    unreviewed_audits = sum(index not in selected for index in audits)
    return VideoReviewSelection(
        candidate_count=len(candidate_frames),
        pool_frames=pool,
        review_frames=review_frames,
        deferred_count=len(candidate_frames) - len(review_frames),
        unreviewed_event_count=unreviewed_events,
        unreviewed_audit_count=unreviewed_audits,
        scene_calls=scene_calls,
        scene_errors=tuple(scene_errors),
        assessments=tuple(assessments),
    )


@dataclass(frozen=True)
class SceneBatchOutcome:
    assessments: tuple[VideoSceneAssessment, ...]
    scene_calls: int
    scene_errors: tuple[str, ...]


def _scene_context(
    run: dict,
    source: AssetRef,
    frames: Sequence[tuple[int, AssetRef, Path]],
) -> dict:
    config = run["config"]
    return {
        "run_id": run["run_id"],
        "source": source.model_dump(mode="json"),
        "frames": [
            {"frame_index": index, "asset": asset.model_dump(mode="json")}
            for index, asset, _ in frames
        ],
        "pins": {
            "video_selection_version": config.get("video_selection_version"),
            "video_scene_prompt_version": VIDEO_SCENE_PROMPT_VERSION,
            "vlm_model": config.get("vlm_model"),
            "vlm_base_url": config.get("vlm_base_url"),
            "api_timeout_seconds": config.get("api_timeout_seconds"),
            "api_enable_thinking": config.get("api_enable_thinking"),
            "api_max_output_tokens": min(config.get("api_max_output_tokens") or 4096, 4096),
            "max_image_bytes": config.get("max_image_bytes"),
        },
    }


def _scene_call(
    run: dict,
    step: str,
    assets: tuple[AssetRef, ...],
    *,
    attempt: int,
    status: str,
    code: str | None = None,
    retryable: bool = False,
) -> ToolCall:
    return ToolCall(
        call_id=uuid5(UUID(run["run_id"]), step),
        task_id=run["task_id"],
        step_id=step,
        idempotency_key=f"{run['run_id']}:{step}",
        attempt=attempt,
        tool_name="verify",
        tool_version=VIDEO_SCENE_PROMPT_VERSION,
        resource="api",
        model_version=run["config"]["vlm_model"],
        timeout_seconds=SCENE_WALLCLOCK_SECONDS,
        input_assets=assets,
        status=status,
        error=(
            ToolError(code=code, retryable=retryable, message=f"视频场景预览未完成：{code}")
            if code is not None
            else None
        ),
    )


def _validate_scene_checkpoint(payload: dict, context: dict) -> tuple[VideoSceneBatch, str | None]:
    if not isinstance(payload, dict) or payload.get("context") != context:
        raise ApplicationError(
            "Video scene checkpoint differs from pinned run",
            type="configuration_changed",
            non_retryable=True,
        )
    try:
        batch = VideoSceneBatch.model_validate(payload["batch"])
        error = payload.get("error")
        if error is not None and (not isinstance(error, str) or not error.strip()):
            raise ValueError
        expected = [item["frame_index"] for item in context["frames"]]
        actual = [item.frame_index for item in batch.assessments]
        if len(actual) != len(set(actual)) or sorted(actual) != sorted(expected):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise ApplicationError(
            "Video scene checkpoint is invalid",
            type="configuration_changed",
            non_retryable=True,
        ) from None
    return batch, error


async def classify_scene_batches(
    repository,
    run: dict,
    source: AssetRef,
    frames: Sequence[tuple[int, AssetRef, Path]],
    selector: VideoSceneSelector | None,
    *,
    budget: int,
    force_review: bool = False,
    heartbeat: Callable[[int, float], None] | None = None,
    before_request: Callable[[], VideoSceneSelector] | None = None,
    attempt: int = 1,
) -> SceneBatchOutcome:
    """Classify and checkpoint bounded scene batches, preserving unknown on failures."""
    if run.get("config", {}).get("video_selection_version") != VIDEO_SELECTION_VERSION:
        raise ApplicationError(
            "Video selection configuration changed",
            type="configuration_changed",
            non_retryable=True,
        )
    if isinstance(budget, bool) or not isinstance(budget, int) or budget < 0:
        raise ValueError("budget must be a non-negative integer")
    if force_review or len(frames) < 4 or budget < 2:
        return SceneBatchOutcome((), 0, ())
    indices = [item[0] for item in frames]
    if len(indices) != len(set(indices)):
        raise ValueError("scene candidate frame indices must be unique")
    batch_count = min(4, ceil(len(frames) / 16), budget - 1)
    task_id = UUID(run["task_id"])
    assessments: list[VideoSceneAssessment] = []
    errors: list[str] = []
    for batch_index in range(batch_count):
        selected = tuple(frames[batch_index * 16 : (batch_index + 1) * 16])
        context = _scene_context(run, source, selected)
        step = f"video_scene:{batch_index}"
        cached = repository.load_step(task_id, step)
        if cached is not None:
            batch, error = _validate_scene_checkpoint(cached, context)
            assessments.extend(batch.assessments)
            if error is not None:
                errors.append(error)
            continue
        if heartbeat is not None:
            for frame_index, _, _ in selected:
                heartbeat(frame_index, 0)
        if before_request is not None:
            selector = before_request()
        if selector is None:
            raise ValueError("scene selector is required for an uncached batch")
        assets = tuple(asset for _, asset, _ in selected)
        repository.record_call(
            _scene_call(run, step, assets, attempt=attempt, status="running")
        )
        error = None
        try:
            scene_frames = tuple((index, path) for index, _, path in selected)
            if isinstance(selector, VideoSceneSelector):
                selected_batch = await selector.classify(
                    scene_frames,
                    heartbeat=(
                        (lambda index: heartbeat(index, 0))
                        if heartbeat is not None
                        else None
                    ),
                )
            else:
                selected_batch = await selector.classify(scene_frames)
            batch = VideoSceneBatch.model_validate(selected_batch)
            returned = [item.frame_index for item in batch.assessments]
            expected_indices = indices[batch_index * 16 : (batch_index + 1) * 16]
            if len(returned) != len(set(returned)) or sorted(returned) != sorted(
                expected_indices
            ):
                raise APIClientError("response_schema_invalid", retryable=False)
        except (APIClientError, OSError, TypeError, ValueError) as exc:
            error = exc.code if isinstance(exc, APIClientError) else "scene_input_invalid"
            retryable = isinstance(exc, APIClientError) and exc.retryable
            batch = VideoSceneBatch(
                assessments=tuple(
                    VideoSceneAssessment(
                        frame_index=index,
                        scene="unknown",
                        reason=f"scene_error:{error}",
                    )
                    for index, _, _ in selected
                )
            )
            final_call = _scene_call(
                run,
                step,
                assets,
                attempt=attempt,
                status="uncertain" if retryable else "failed",
                code=error,
                retryable=retryable,
            )
        payload = {
            "context": context,
            "batch": batch.model_dump(mode="json"),
            "error": error,
        }
        if error is None:
            saved = repository.save_step(
                task_id,
                step,
                payload,
                _scene_call(run, step, assets, attempt=attempt, status="succeeded"),
            )
        else:
            saved = repository.save_step(
                task_id,
                step,
                payload,
                final_call,
                allow_error_checkpoint=True,
            )
        batch, error = _validate_scene_checkpoint(saved, context)
        assessments.extend(batch.assessments)
        if error is not None:
            errors.append(error)
        if heartbeat is not None:
            heartbeat(selected[-1][0], 0)
    return SceneBatchOutcome(tuple(assessments), batch_count, tuple(errors))
