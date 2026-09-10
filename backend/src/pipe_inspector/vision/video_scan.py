"""Initial video scan, designed to be called by the later durable chunk workflow."""

import shutil
from contextlib import closing
from pathlib import Path

from pipe_inspector.contracts.video import (
    FrameObservation,
    VideoAggregationConfig,
    VideoAggregationState,
    VideoEvidenceArtifact,
    VideoScanResult,
)
from pipe_inspector.vision.quality import analyze_quality
from pipe_inspector.vision.video_decode import VideoDecodeError, decode_video_chunks
from pipe_inspector.vision.video_events import aggregate_chunk


def scan_video(
    source: Path,
    source_ref,
    detector,
    *,
    chunk_size=8,
    timeout_seconds=30,
    max_pixels=40_000_000,
    scratch_root=None,
    config=None,
    on_chunk=None,
    initial_state=None,
    on_progress=None,
) -> VideoScanResult:
    metadata = {
        key: getattr(detector, key)
        for key in ("detector_id", "model_version", "label_space_version")
    }
    state = initial_state or VideoAggregationState(
        source=source_ref, config=config or VideoAggregationConfig(), **metadata
    )
    if state.source != source_ref:
        raise ValueError("resume source changed")
    if any(getattr(state, key) != value for key, value in metadata.items()):
        raise ValueError("resume detector metadata changed")
    if config is not None and state.config != config:
        raise ValueError("resume aggregation config changed")
    if state.finalized:
        raise ValueError("cannot resume finalized scan")
    initial_cursor = state.next_frame
    observed_count = 0

    def progress(index, timestamp):
        nonlocal observed_count
        observed_count = index + 1
        if initial_cursor and index == initial_cursor - 1:
            if timestamp != initial_state.last_timestamp_seconds:
                raise VideoDecodeError("evidence_source_changed")
        if on_progress:
            on_progress(index, timestamp)

    error = None
    try:
        with closing(
            decode_video_chunks(
                source,
                chunk_size=chunk_size,
                timeout_seconds=timeout_seconds,
                max_pixels=max_pixels,
                scratch_root=scratch_root,
                start_frame=state.next_frame,
                on_progress=progress,
            )
        ) as chunks:
            for frames in chunks:
                # A competing/retried commit may have already advanced the durable head.
                frames = tuple(frame for frame in frames if frame.index >= state.next_frame)
                if not frames:
                    continue
                try:
                    paths = tuple(frame.path for frame in frames)
                    if detector.capabilities.supports_batch:
                        batches = detector.predict_batch(paths)
                    else:
                        batches = tuple(detector.predict_image(path) for path in paths)
                except (OSError, RuntimeError, ValueError):
                    error = "detection_failed"
                    break
                if len(batches) != len(frames) or any(
                    (batch.width, batch.height) != (frame.width, frame.height)
                    or any(getattr(batch, key) != value for key, value in metadata.items())
                    for frame, batch in zip(frames, batches)
                ):
                    error = "invalid_detection_batch"
                    break
                observations = tuple(
                    FrameObservation(
                        frame_index=frame.index,
                        timestamp_seconds=frame.timestamp_seconds,
                        detection=batch,
                        quality=analyze_quality(frame.path),
                    )
                    for frame, batch in zip(frames, batches, strict=True)
                )
                next_state = aggregate_chunk(state, observations)
                if on_chunk:
                    committed = on_chunk(observations, next_state)
                    if committed is not None:
                        if (
                            committed.source != state.source
                            or committed.config != state.config
                            or committed.finalized
                            or committed.next_frame < next_state.next_frame
                            or any(
                                getattr(committed, key) != value for key, value in metadata.items()
                            )
                        ):
                            raise ValueError("invalid durable scan cursor")
                        next_state = committed
                state = next_state
    except VideoDecodeError as exc:
        error = exc.code
    if error is None and observed_count < state.next_frame:
        error = "evidence_source_changed"
    if state.next_frame == 0 and error is None:
        error = "empty_video"
    return VideoScanResult(
        state=aggregate_chunk(state, (), final=True),
        decode_complete=error is None,
        error_code=error,
    )


def extract_scan_evidence(
    source: Path,
    result: VideoScanResult,
    output: Path,
    *,
    chunk_size=8,
    timeout_seconds=30,
    max_pixels=40_000_000,
    scratch_root=None,
) -> tuple[VideoEvidenceArtifact, ...]:
    """Materialize selected originals via a second decode-only pass, never re-inference."""
    selected = {item.frame_index: item for item in result.state.audit_frames}
    for event in result.state.events:
        selected.update((item.frame_index, item) for item in event.evidence)
    output.mkdir(parents=True, exist_ok=False)
    if not selected:
        return ()
    artifacts = []
    with closing(
        decode_video_chunks(
            source,
            chunk_size=chunk_size,
            timeout_seconds=timeout_seconds,
            max_pixels=max_pixels,
            scratch_root=scratch_root,
            selected_frames=frozenset(selected),
        )
    ) as chunks:
        for frames in chunks:
            for frame in frames:
                expected = selected.get(frame.index)
                if expected is None:
                    continue
                if (frame.timestamp_seconds, frame.width, frame.height) != (
                    expected.timestamp_seconds,
                    expected.width,
                    expected.height,
                ):
                    raise VideoDecodeError("evidence_source_changed")
                name = f"{frame.index:09d}.png"
                with frame.path.open("rb") as src, (output / name).open("xb") as dest:
                    shutil.copyfileobj(src, dest)
                artifacts.append(
                    VideoEvidenceArtifact(
                        frame_index=frame.index,
                        timestamp_seconds=frame.timestamp_seconds,
                        width=frame.width,
                        height=frame.height,
                        quality=expected.quality,
                        relative_path=name,
                    )
                )
            if len(artifacts) == len(selected):
                break
    if len(artifacts) != len(selected):
        raise VideoDecodeError("evidence_incomplete")
    return tuple(artifacts)
