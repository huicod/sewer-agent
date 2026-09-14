"""Bounded, timestamp-preserving video frame decoding."""

from __future__ import annotations

import math
import os
import re
import selectors
import shutil
import subprocess
import tempfile
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Literal

from PIL import Image


@dataclass(frozen=True)
class DecodedFrame:
    index: int
    timestamp_seconds: float
    width: int
    height: int
    path: Path


class VideoDecodeError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class _FrameInfo:
    index: int
    timestamp_seconds: float
    width: int
    height: int


_SHOWINFO_INDEX = re.compile(r"\bn:\s*(\d+)")
_SHOWINFO_PTS = re.compile(r"\bpts:\s*([^\s]+)")
_SHOWINFO_SIZE = re.compile(r"\bs:\s*(\d+)x(\d+)")
_SHOWINFO_TIME_BASE = re.compile(r"\bconfig in time_base:\s*(\d+)/(\d+)")
_MAX_STDERR_LINE_BYTES = 64 * 1024


def _parse_showinfo(
    line: str,
    *,
    expected_index: int,
    previous_pts: int | None,
    time_base: tuple[int, int] | None,
    max_pixels: int,
) -> _FrameInfo | None:
    if "Parsed_showinfo" not in line or " n:" not in line:
        return None
    index_match = _SHOWINFO_INDEX.search(line)
    pts_match = _SHOWINFO_PTS.search(line)
    size_match = _SHOWINFO_SIZE.search(line)
    if pts_match is None or time_base is None:
        raise VideoDecodeError("invalid_timestamp")
    if index_match is None or size_match is None:
        raise VideoDecodeError("invalid_frame_metadata")
    index = int(index_match.group(1))
    try:
        pts = int(pts_match.group(1))
    except ValueError as exc:
        raise VideoDecodeError("invalid_timestamp") from exc
    numerator, denominator = time_base
    timestamp = pts * numerator / denominator
    width, height = int(size_match.group(1)), int(size_match.group(2))
    if index != expected_index:
        raise VideoDecodeError("invalid_frame_metadata")
    if (
        not math.isfinite(timestamp)
        or timestamp < 0
        or (previous_pts is not None and pts < previous_pts)
    ):
        raise VideoDecodeError("invalid_timestamp")
    if width <= 0 or height <= 0:
        raise VideoDecodeError("invalid_dimensions")
    if width * height > max_pixels:
        raise VideoDecodeError("frame_too_large")
    return _FrameInfo(index, timestamp, width, height)


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1)
    for stream in (process.stdout, process.stderr):
        if stream is not None:
            stream.close()


def _raw_frames(
    process: subprocess.Popen[bytes], *, timeout_seconds: float, max_pixels: int
) -> Iterator[tuple[_FrameInfo, bytes]]:
    if (
        process.stdout is None or process.stderr is None
    ):  # pragma: no cover - construction invariant
        raise VideoDecodeError("decode_failed")

    selector = selectors.DefaultSelector()
    stdout_fd = process.stdout.fileno()
    stderr_fd = process.stderr.fileno()
    os.set_blocking(stdout_fd, False)
    os.set_blocking(stderr_fd, False)
    selector.register(stdout_fd, selectors.EVENT_READ, "stdout")
    selector.register(stderr_fd, selectors.EVENT_READ, "stderr")
    output = bytearray()
    stderr_line = bytearray()
    metadata: deque[_FrameInfo] = deque()
    expected_index = 0
    previous_pts: int | None = None
    time_base: tuple[int, int] | None = None
    deadline = time.monotonic() + timeout_seconds
    max_raw_backlog = max_pixels * 3 + 64 * 1024

    try:
        while selector.get_map():
            while metadata:
                info = metadata[0]
                byte_count = info.width * info.height * 3
                if len(output) < byte_count:
                    break
                metadata.popleft()
                pixels = bytes(output[:byte_count])
                del output[:byte_count]
                yield info, pixels
                deadline = time.monotonic() + timeout_seconds

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise VideoDecodeError("decode_timeout")
            events = selector.select(remaining)
            if not events:
                raise VideoDecodeError("decode_timeout")
            # Metadata is consumed first when both pipes are ready, bounding raw buffering.
            events.sort(key=lambda event: event[0].data == "stdout")
            for key, _ in events:
                try:
                    data = os.read(key.fd, 64 * 1024)
                except BlockingIOError:
                    continue
                if not data:
                    selector.unregister(key.fd)
                    continue
                if key.data == "stdout":
                    output.extend(data)
                    if len(output) > max_raw_backlog:
                        raise VideoDecodeError("frame_size_mismatch")
                    continue
                stderr_line.extend(data)
                if len(stderr_line) > _MAX_STDERR_LINE_BYTES and b"\n" not in stderr_line:
                    raise VideoDecodeError("invalid_frame_metadata")
                while newline := stderr_line.find(b"\n") + 1:
                    line = bytes(stderr_line[:newline]).decode("utf-8", errors="replace")
                    del stderr_line[:newline]
                    time_base_match = _SHOWINFO_TIME_BASE.search(line)
                    if time_base_match is not None:
                        candidate = (int(time_base_match.group(1)), int(time_base_match.group(2)))
                        if candidate[0] <= 0 or candidate[1] <= 0:
                            raise VideoDecodeError("invalid_timestamp")
                        if time_base is not None and candidate != time_base:
                            raise VideoDecodeError("invalid_timestamp")
                        time_base = candidate
                    info = _parse_showinfo(
                        line,
                        expected_index=expected_index,
                        previous_pts=previous_pts,
                        time_base=time_base,
                        max_pixels=max_pixels,
                    )
                    if info is not None:
                        metadata.append(info)
                        expected_index += 1
                        pts_match = _SHOWINFO_PTS.search(line)
                        if pts_match is None:  # pragma: no cover - parser checked above
                            raise VideoDecodeError("invalid_timestamp")
                        previous_pts = int(pts_match.group(1))

        while metadata:
            info = metadata.popleft()
            byte_count = info.width * info.height * 3
            if len(output) < byte_count:
                raise VideoDecodeError("frame_size_mismatch")
            pixels = bytes(output[:byte_count])
            del output[:byte_count]
            yield info, pixels
        if output:
            raise VideoDecodeError("frame_size_mismatch")
        return_code = process.wait(timeout=1)
        if return_code != 0 or expected_index == 0:
            raise VideoDecodeError("decode_failed")
    finally:
        selector.close()


def decode_video_chunks(
    source: Path,
    *,
    frame_format: Literal["PNG", "BMP"] = "PNG",
    chunk_size: int = 8,
    timeout_seconds: float = 30,
    max_pixels: int = 40_000_000,
    scratch_root: Path | None = None,
    start_frame: int = 0,
    on_progress: Callable[[int, float], None] | None = None,
    selected_frames: frozenset[int] | None = None,
) -> Iterator[tuple[DecodedFrame, ...]]:
    """Decode all video frames once, yielding temporary lossless paths in bounded chunks."""
    if (
        frame_format not in ("PNG", "BMP")
        or chunk_size <= 0
        or max_pixels <= 0
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
        or not isinstance(start_frame, int)
        or isinstance(start_frame, bool)
        or start_frame < 0
    ):
        raise VideoDecodeError("invalid_options")
    if selected_frames is not None and any(
        not isinstance(index, int) or isinstance(index, bool) or index < 0
        for index in selected_frames
    ):
        raise VideoDecodeError("invalid_options")
    source = Path(source)
    if not source.is_file():
        raise VideoDecodeError("source_unavailable")
    if scratch_root is not None and not Path(scratch_root).is_dir():
        raise VideoDecodeError("scratch_unavailable")

    owned_root = Path(
        tempfile.mkdtemp(prefix="video-decode-", dir=None if scratch_root is None else scratch_root)
    )
    process: subprocess.Popen[bytes] | None = None
    chunk_directory: Path | None = None
    try:
        try:
            process = subprocess.Popen(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-nostdin",
                    "-loglevel",
                    "info",
                    "-nostats",
                    "-xerror",
                    "-protocol_whitelist",
                    "file",
                    "-format_whitelist",
                    "mov,avi,matroska",
                    "-copyts",
                    "-i",
                    str(source),
                    "-map",
                    "0:v:0",
                    "-an",
                    "-sn",
                    "-dn",
                    "-vsync",
                    "0",
                    "-vf",
                    "format=rgb24,showinfo=checksum=0",
                    "-pix_fmt",
                    "rgb24",
                    "-f",
                    "rawvideo",
                    "pipe:1",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
        except FileNotFoundError as exc:
            raise VideoDecodeError("media_tool_unavailable") from exc

        chunk: list[DecodedFrame] = []
        chunk_number = 0
        decode_error: VideoDecodeError | None = None
        raw_frames = _raw_frames(process, timeout_seconds=timeout_seconds, max_pixels=max_pixels)
        try:
            while True:
                try:
                    info, pixels = next(raw_frames)
                except StopIteration:
                    break
                except VideoDecodeError as exc:
                    decode_error = exc
                    break
                if on_progress is not None:
                    on_progress(info.index, info.timestamp_seconds)
                if info.index < start_frame or (
                    selected_frames is not None and info.index not in selected_frames
                ):
                    continue
                if chunk_directory is None:
                    chunk_directory = owned_root / f"chunk-{chunk_number:08d}"
                    chunk_directory.mkdir()
                path = chunk_directory / f"frame-{info.index:012d}.{frame_format.lower()}"
                try:
                    image = Image.frombytes("RGB", (info.width, info.height), pixels)
                    if frame_format == "PNG":
                        image.save(path, format="PNG", compress_level=1)
                    else:
                        image.save(path, format="BMP")
                except (OSError, ValueError) as exc:
                    raise VideoDecodeError("frame_write_failed") from exc
                chunk.append(
                    DecodedFrame(
                        index=info.index,
                        timestamp_seconds=info.timestamp_seconds,
                        width=info.width,
                        height=info.height,
                        path=path,
                    )
                )
                if len(chunk) == chunk_size:
                    yield tuple(chunk)
                    shutil.rmtree(chunk_directory)
                    chunk_directory = None
                    chunk = []
                    chunk_number += 1
        finally:
            raw_frames.close()
        if chunk:
            yield tuple(chunk)
            if chunk_directory is not None:
                shutil.rmtree(chunk_directory)
                chunk_directory = None
        if decode_error is not None:
            raise decode_error
    finally:
        if process is not None:
            _stop_process(process)
        shutil.rmtree(owned_root, ignore_errors=True)
