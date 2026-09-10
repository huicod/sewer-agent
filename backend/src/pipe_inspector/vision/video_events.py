"""Small deterministic temporal association over detector-neutral original-pixel boxes."""

from pipe_inspector.contracts.common import ImageRegion
from pipe_inspector.contracts.video import (
    EventEvidence,
    FrameLocator,
    VideoAggregationState,
    VideoEvent,
)

POLICY_VERSION = "video-events-v1"


def _iou(left: ImageRegion, right: ImageRegion) -> float:
    a = [v / (left.width if i % 2 == 0 else left.height) for i, v in enumerate(left.bbox_xyxy)]
    b = [v / (right.width if i % 2 == 0 else right.height) for i, v in enumerate(right.bbox_xyxy)]
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0, min(a[3], b[3]) - max(a[1], b[1])
    )
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection
    return intersection / union


def _rank(item):
    return (
        item.quality.adequate,
        getattr(item, "score", 0),
        item.quality.sharpness,
        item.quality.contrast,
        -item.frame_index,
    )


def _evidence(items, separation):
    selected = []
    for item in sorted(items, key=_rank, reverse=True):
        if all(
            abs(item.timestamp_seconds - old.timestamp_seconds) >= separation
            and item.frame_index != old.frame_index
            for old in selected
        ):
            selected.append(item)
        if len(selected) == 3:
            break
    return tuple(sorted(selected, key=lambda item: item.frame_index))


def aggregate_chunk(
    state: VideoAggregationState, observations, *, final=False
) -> VideoAggregationState:
    if state.config.policy_version != POLICY_VERSION:
        raise ValueError("video aggregation policy differs from saved state")
    active, events, audit = list(state.active), list(state.events), list(state.audit_frames)
    next_frame, last_time, adequate = (
        state.next_frame,
        state.last_timestamp_seconds,
        state.adequate_frames,
    )
    pins = {
        key: getattr(state, key) for key in ("detector_id", "model_version", "label_space_version")
    }
    chunk_previous = -1
    for frame in observations:
        if frame.frame_index <= chunk_previous:
            raise ValueError("chunk frames must be strictly increasing")
        chunk_previous = frame.frame_index
        metadata = {key: getattr(frame.detection, key) for key in pins}
        if pins["detector_id"] is not None and pins != metadata:
            raise ValueError("detector metadata differs from saved video state")
        if frame.frame_index < next_frame:
            continue  # Replay prefix under the same pinned source/run, not a second observation.
        if state.finalized:
            raise ValueError("cannot append frames after scan finalization")
        if frame.frame_index != next_frame:
            raise ValueError("unprocessed frame gap; coverage cannot be silently skipped")
        if last_time is not None and frame.timestamp_seconds < last_time:
            raise ValueError("source timestamps must not go backwards")
        pins = metadata
        remaining = []
        for event in active:
            if (
                frame.frame_index - event.end_frame > state.config.max_gap_frames + 1
                or frame.timestamp_seconds - event.end_seconds > state.config.max_gap_seconds
            ):
                events.append(event)
            else:
                remaining.append(event)
        active = remaining
        batch = frame.detection
        regions = [
            ImageRegion(width=batch.width, height=batch.height, bbox_xyxy=d.bbox_xyxy)
            for d in batch.detections
        ]
        matches = []
        for i, event in enumerate(active):
            for j, detection in enumerate(batch.detections):
                if event.label == detection.label:
                    overlap = _iou(event.last_region, regions[j])
                    if overlap >= state.config.iou_threshold:
                        matches.append((-overlap, event.event_id, i, j))
        assigned, used = {}, set()
        for _, _, i, j in sorted(matches):
            if i not in used and j not in assigned:
                assigned[j] = i
                used.add(i)
        locator = FrameLocator(
            frame_index=frame.frame_index,
            timestamp_seconds=frame.timestamp_seconds,
            width=batch.width,
            height=batch.height,
            quality=frame.quality,
        )
        for j, detection in enumerate(batch.detections):
            evidence = EventEvidence(
                **locator.model_dump(), region=regions[j], score=detection.score
            )
            if j in assigned:
                i = assigned[j]
                event = active[i]
                active[i] = VideoEvent.model_validate(
                    event.model_dump()
                    | {
                        "end_frame": frame.frame_index,
                        "end_seconds": frame.timestamp_seconds,
                        "observations": event.observations + 1,
                        "last_region": regions[j],
                        "evidence": _evidence(
                            (*event.evidence, evidence), state.config.evidence_separation_seconds
                        ),
                    }
                )
            else:
                active.append(
                    VideoEvent(
                        event_id=f"{state.source.asset_id}:v{state.source.version}:{frame.frame_index}:{j}",
                        label=detection.label,
                        start_frame=frame.frame_index,
                        end_frame=frame.frame_index,
                        start_seconds=frame.timestamp_seconds,
                        end_seconds=frame.timestamp_seconds,
                        observations=1,
                        last_region=regions[j],
                        evidence=(evidence,),
                    )
                )
        bucket = int(frame.timestamp_seconds / state.config.audit_interval_seconds)
        if (
            audit
            and int(audit[-1].timestamp_seconds / state.config.audit_interval_seconds) == bucket
        ):
            if _rank(locator) > _rank(audit[-1]):
                audit[-1] = locator
        else:
            audit.append(locator)
        next_frame += 1
        last_time = frame.timestamp_seconds
        adequate += int(frame.quality.adequate)
    if final:
        events.extend(active)
        active = []
    return VideoAggregationState(
        source=state.source,
        config=state.config,
        **pins,
        next_frame=next_frame,
        last_timestamp_seconds=last_time,
        adequate_frames=adequate,
        active=tuple(active),
        events=tuple(sorted(events, key=lambda e: (e.start_frame, e.event_id))),
        audit_frames=tuple(audit),
        finalized=state.finalized or final,
    )
