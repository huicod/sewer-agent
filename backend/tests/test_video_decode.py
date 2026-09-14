from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest
from PIL import Image
from pipe_inspector.vision.video_decode import (
    DecodedFrame,
    VideoDecodeError,
    decode_video_chunks,
)


def _make_cfr_video(path: Path, *, frames: int = 5, start_seconds: float = 3.0) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=s=18x12:r=5:d={frames / 5}",
            "-output_ts_offset",
            str(start_seconds),
            "-c:v",
            "ffv1",
            str(path),
        ],
        check=True,
        timeout=20,
    )


def _make_vfr_video(path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=20x14:r=10:d=0.4",
            "-vf",
            "settb=expr=1/1000,setpts=2000+100*N*N",
            "-vsync",
            "vfr",
            "-c:v",
            "ffv1",
            str(path),
        ],
        check=True,
        timeout=20,
    )


def test_temporary_png_uses_fast_lossless_encoding(tmp_path, monkeypatch):
    source = tmp_path / "fast.mkv"
    _make_cfr_video(source, frames=1)
    original_save = Image.Image.save
    options = []

    def save(image, *args, **kwargs):
        options.append(kwargs)
        return original_save(image, *args, **kwargs)

    monkeypatch.setattr(Image.Image, "save", save)
    for chunk in decode_video_chunks(source):
        assert chunk[0].path.suffix == ".png"
        with Image.open(chunk[0].path) as image:
            assert image.format == "PNG"
            assert image.size == (18, 12) and image.mode == "RGB"
    assert options == [{"format": "PNG", "compress_level": 1}]


def test_bmp_frames_preserve_default_png_pixels_and_pts(tmp_path):
    source = tmp_path / "lossless.mkv"
    _make_cfr_video(source, frames=3)

    png_decoder = decode_video_chunks(source)
    png_frames = next(png_decoder)
    png_pixels = []
    for frame in png_frames:
        assert frame.path.suffix == ".png"
        with Image.open(frame.path) as image:
            assert image.format == "PNG"
            png_pixels.append(image.convert("RGB").tobytes())
    png_pts = [frame.timestamp_seconds for frame in png_frames]
    png_decoder.close()

    bmp_decoder = decode_video_chunks(source, frame_format="BMP")
    bmp_frames = next(bmp_decoder)
    bmp_pixels = []
    for frame in bmp_frames:
        assert frame.path.suffix == ".bmp"
        assert frame.path.read_bytes()[30:34] == b"\x00\x00\x00\x00"
        with Image.open(frame.path) as image:
            assert image.format == "BMP"
            bmp_pixels.append(image.convert("RGB").tobytes())
    bmp_pts = [frame.timestamp_seconds for frame in bmp_frames]
    bmp_decoder.close()

    assert bmp_pixels == png_pixels
    assert bmp_pts == pytest.approx(png_pts)


def test_rejects_unknown_frame_format_before_creating_scratch_directory(tmp_path):
    source = tmp_path / "source.mkv"
    source.touch()
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    with pytest.raises(VideoDecodeError) as caught:
        list(
            decode_video_chunks(
                source,
                frame_format="JPEG",  # type: ignore[arg-type]
                scratch_root=scratch,
            )
        )

    assert caught.value.code == "invalid_options"
    assert not list(scratch.iterdir())


def test_sparse_evidence_only_materializes_selected_originals(tmp_path, monkeypatch):
    source = tmp_path / "sparse.mkv"
    _make_cfr_video(source, frames=6)
    original_save = Image.Image.save
    saved, progress, emitted = [], [], []

    def save(image, path, **kwargs):
        saved.append(path.name)
        return original_save(image, path, **kwargs)

    monkeypatch.setattr(Image.Image, "save", save)
    for chunk in decode_video_chunks(
        source,
        selected_frames=frozenset({1, 4}),
        on_progress=lambda index, _: progress.append(index),
    ):
        emitted.extend((frame.index, frame.timestamp_seconds) for frame in chunk)
    assert emitted == [(1, 3.2), (4, 3.8)]
    assert progress == list(range(6))
    assert len(saved) == 2


def _fake_ffmpeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shell_body: str) -> Path:
    executable_directory = tmp_path / "bin"
    executable_directory.mkdir()
    executable = executable_directory / "ffmpeg"
    config = "printf '[Parsed_showinfo_1 @ x] config in time_base: 1/1, frame_rate: 1/1\\n' >&2\n"
    executable.write_text("#!/bin/sh\n" + config + shell_body, encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(executable_directory))
    source = tmp_path / "source.mkv"
    source.touch()
    return source


def test_streams_every_cfr_frame_in_bounded_chunks_with_original_pts(tmp_path: Path) -> None:
    source = tmp_path / "clip with spaces.mkv"
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    _make_cfr_video(source)

    decoder = decode_video_chunks(source, chunk_size=2, scratch_root=scratch)
    first = next(decoder)
    assert [frame.index for frame in first] == [0, 1]
    assert [frame.timestamp_seconds for frame in first] == pytest.approx([3.0, 3.2])
    assert all(isinstance(frame, DecodedFrame) for frame in first)
    assert all((frame.width, frame.height) == (18, 12) for frame in first)
    assert all(frame.path.is_file() for frame in first)
    with Image.open(first[0].path) as image:
        assert image.mode == "RGB"
        assert image.size == (18, 12)
        assert len(image.tobytes()) == 18 * 12 * 3

    second = next(decoder)
    assert [frame.index for frame in second] == [2, 3]
    assert [frame.timestamp_seconds for frame in second] == pytest.approx([3.4, 3.6])
    assert not any(frame.path.exists() for frame in first)
    assert all(frame.path.is_file() for frame in second)

    third = next(decoder)
    assert [frame.index for frame in third] == [4]
    assert [frame.timestamp_seconds for frame in third] == pytest.approx([3.8])
    assert not any(frame.path.exists() for frame in second)

    with pytest.raises(StopIteration):
        next(decoder)
    assert not list(scratch.iterdir())


def test_preserves_hand_known_vfr_timestamps(tmp_path: Path) -> None:
    source = tmp_path / "vfr.mkv"
    _make_vfr_video(source)

    frames = [frame for chunk in decode_video_chunks(source, chunk_size=8) for frame in chunk]

    assert [frame.index for frame in frames] == [0, 1, 2, 3]
    assert [frame.timestamp_seconds for frame in frames] == pytest.approx([2.0, 2.1, 2.4, 2.9])


def test_integer_pts_and_time_base_preserve_adjacent_large_offset_frames(tmp_path: Path) -> None:
    source = tmp_path / "large-offset.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=16x12:r=1000:d=0.002",
            "-output_ts_offset",
            "100000",
            "-c:v",
            "ffv1",
            str(source),
        ],
        check=True,
        timeout=20,
    )

    frames = [frame for chunk in decode_video_chunks(source) for frame in chunk]

    assert [frame.timestamp_seconds for frame in frames] == pytest.approx(
        [100000.0, 100000.001], abs=1e-9
    )
    assert frames[1].timestamp_seconds > frames[0].timestamp_seconds


def test_resume_decodes_and_reports_all_frames_but_materializes_only_suffix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "resume.mkv"
    _make_cfr_video(source, frames=6, start_seconds=2)
    progress: list[tuple[int, float]] = []
    saved_paths: list[Path] = []
    original_save = Image.Image.save

    def tracking_save(image: Image.Image, path: Path, *args: object, **kwargs: object) -> None:
        saved_paths.append(Path(path))
        original_save(image, path, *args, **kwargs)

    monkeypatch.setattr(Image.Image, "save", tracking_save)

    frames = [
        frame
        for chunk in decode_video_chunks(
            source,
            start_frame=3,
            on_progress=lambda index, timestamp: progress.append((index, timestamp)),
        )
        for frame in chunk
    ]

    assert [frame.index for frame in frames] == [3, 4, 5]
    assert [frame.timestamp_seconds for frame in frames] == pytest.approx([2.6, 2.8, 3.0])
    assert [item[0] for item in progress] == [0, 1, 2, 3, 4, 5]
    assert [item[1] for item in progress] == pytest.approx([2.0, 2.2, 2.4, 2.6, 2.8, 3.0])
    assert [path.name for path in saved_paths] == [
        "frame-000000000003.png",
        "frame-000000000004.png",
        "frame-000000000005.png",
    ]


def test_start_past_eof_yields_nothing_but_reports_complete_replay(tmp_path: Path) -> None:
    source = tmp_path / "past-eof.mkv"
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    _make_cfr_video(source, frames=2, start_seconds=0)
    progress: list[int] = []

    chunks = list(
        decode_video_chunks(
            source,
            start_frame=20,
            on_progress=lambda index, _timestamp: progress.append(index),
            scratch_root=scratch,
        )
    )

    assert chunks == []
    assert progress == [0, 1]
    assert not list(scratch.iterdir())


def test_progress_callback_exception_propagates_and_cleans_decoder_files(tmp_path: Path) -> None:
    source = tmp_path / "cancel.mkv"
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    _make_cfr_video(source, frames=6, start_seconds=0)
    progress: list[int] = []

    def cancel_at_frame_four(index: int, _timestamp: float) -> None:
        progress.append(index)
        if index == 4:
            raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        list(
            decode_video_chunks(
                source,
                start_frame=3,
                on_progress=cancel_at_frame_four,
                scratch_root=scratch,
            )
        )

    assert progress == [0, 1, 2, 3, 4]
    assert not list(scratch.iterdir())


@pytest.mark.parametrize("start_frame", [-1, 1.5, "1", True, None])
def test_rejects_invalid_start_frame(tmp_path: Path, start_frame: object) -> None:
    source = tmp_path / "source.mkv"
    source.touch()

    with pytest.raises(VideoDecodeError) as caught:
        list(decode_video_chunks(source, start_frame=start_frame))  # type: ignore[arg-type]

    assert caught.value.code == "invalid_options"


def test_close_removes_the_current_chunk_and_owned_scratch_directory(tmp_path: Path) -> None:
    source = tmp_path / "early-close.mkv"
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    _make_cfr_video(source, frames=20, start_seconds=0)
    decoder = decode_video_chunks(source, chunk_size=2, scratch_root=scratch)

    current = next(decoder)
    assert all(frame.path.exists() for frame in current)
    decoder.close()

    assert not list(scratch.iterdir())


def test_rejects_a_frame_over_the_pixel_limit_and_cleans_scratch(tmp_path: Path) -> None:
    source = tmp_path / "large.mkv"
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    _make_cfr_video(source, frames=1, start_seconds=0)

    with pytest.raises(VideoDecodeError) as caught:
        list(decode_video_chunks(source, max_pixels=100, scratch_root=scratch))

    assert caught.value.code == "frame_too_large"
    assert not list(scratch.iterdir())


def test_rejects_non_video_container_without_leaving_scratch(tmp_path: Path) -> None:
    source = tmp_path / "picture.gif"
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    Image.new("RGB", (4, 3), "red").save(source)

    with pytest.raises(VideoDecodeError) as caught:
        list(decode_video_chunks(source, scratch_root=scratch))

    assert caught.value.code == "decode_failed"
    assert not list(scratch.iterdir())


@pytest.mark.parametrize("timestamp", ["-1", "nan", "inf"])
def test_rejects_invalid_pts_from_ffmpeg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, timestamp: str
) -> None:
    source = _fake_ffmpeg(
        tmp_path,
        monkeypatch,
        "printf '[Parsed_showinfo_1 @ x] n: 0 pts:"
        + timestamp
        + " pts_time:"
        + timestamp
        + " s:1x1\\n' >&2\nprintf '\\001\\002\\003'\n",
    )

    with pytest.raises(VideoDecodeError) as caught:
        list(decode_video_chunks(source))

    assert caught.value.code == "invalid_timestamp"


def test_rejects_missing_pts_from_ffmpeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _fake_ffmpeg(
        tmp_path,
        monkeypatch,
        "printf '[Parsed_showinfo_1 @ x] n: 0 pts: NOPTS s:1x1\\n' >&2\nprintf '\\001\\002\\003'\n",
    )

    with pytest.raises(VideoDecodeError) as caught:
        list(decode_video_chunks(source))

    assert caught.value.code == "invalid_timestamp"


def test_rejects_decreasing_pts_from_ffmpeg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _fake_ffmpeg(
        tmp_path,
        monkeypatch,
        "printf '[Parsed_showinfo_1 @ x] n: 0 pts: 2 pts_time:2 s:1x1\\n' >&2\n"
        "printf '[Parsed_showinfo_1 @ x] n: 1 pts: 1 pts_time:1 s:1x1\\n' >&2\n"
        "printf '\\001\\002\\003\\004\\005\\006'\n",
    )

    with pytest.raises(VideoDecodeError) as caught:
        list(decode_video_chunks(source))

    assert caught.value.code == "invalid_timestamp"


def test_accepts_dimension_changes_when_each_frame_is_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _fake_ffmpeg(
        tmp_path,
        monkeypatch,
        "printf '[Parsed_showinfo_1 @ x] n: 0 pts: 0 pts_time:0 s:1x1\\n' >&2\n"
        "printf '[Parsed_showinfo_1 @ x] n: 1 pts: 1 pts_time:1 s:2x1\\n' >&2\n"
        "printf '\\001\\002\\003\\004\\005\\006\\007\\010\\011'\n",
    )

    decoder = decode_video_chunks(source, chunk_size=8)
    chunk = next(decoder)

    assert [(frame.width, frame.height) for frame in chunk] == [(1, 1), (2, 1)]
    with Image.open(chunk[0].path) as first:
        assert first.tobytes() == b"\x01\x02\x03"
    with Image.open(chunk[1].path) as second:
        assert second.tobytes() == b"\x04\x05\x06\x07\x08\x09"
    decoder.close()


def test_yields_complete_prefix_chunk_before_reporting_ffmpeg_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _fake_ffmpeg(
        tmp_path,
        monkeypatch,
        "printf '[Parsed_showinfo_1 @ x] n: 0 pts: 0 pts_time:0 s:1x1\\n' >&2\n"
        "printf '[Parsed_showinfo_1 @ x] n: 1 pts: 1 pts_time:1 s:1x1\\n' >&2\n"
        "printf '\\001\\002\\003\\004\\005\\006'\n"
        "exit 7\n",
    )
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    decoder = decode_video_chunks(source, scratch_root=scratch)

    prefix = next(decoder)
    assert [frame.index for frame in prefix] == [0, 1]
    assert all(frame.path.is_file() for frame in prefix)

    with pytest.raises(VideoDecodeError) as caught:
        next(decoder)

    assert caught.value.code == "decode_failed"
    assert not any(frame.path.exists() for frame in prefix)
    assert not list(scratch.iterdir())


def test_rejects_raw_frame_byte_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _fake_ffmpeg(
        tmp_path,
        monkeypatch,
        "printf '[Parsed_showinfo_1 @ x] n: 0 pts: 0 pts_time:0 s:2x2\\n' >&2\n"
        "printf '\\001\\002\\003'\n",
    )

    with pytest.raises(VideoDecodeError) as caught:
        list(decode_video_chunks(source))

    assert caught.value.code == "frame_size_mismatch"


def test_times_out_a_stalled_ffmpeg_process_and_cleans_scratch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _fake_ffmpeg(tmp_path, monkeypatch, "/bin/sleep 5\n")
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    with pytest.raises(VideoDecodeError) as caught:
        list(decode_video_chunks(source, timeout_seconds=0.05, scratch_root=scratch))

    assert caught.value.code == "decode_timeout"
    assert not list(scratch.iterdir())


def test_continuous_non_frame_logs_do_not_extend_the_decode_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _fake_ffmpeg(
        tmp_path,
        monkeypatch,
        'i=0\nwhile [ "$i" -lt 30 ]; do\n'
        "  printf 'noise\\n' >&2\n  i=$((i + 1))\n  /bin/sleep 0.01\ndone\n/bin/sleep 5\n",
    )

    started = time.monotonic()
    with pytest.raises(VideoDecodeError) as caught:
        list(decode_video_chunks(source, timeout_seconds=0.05))

    assert caught.value.code == "decode_timeout"
    assert time.monotonic() - started < 0.2


def test_unterminated_stderr_is_bounded_and_fails_safely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _fake_ffmpeg(
        tmp_path,
        monkeypatch,
        "/usr/bin/head -c 70000 /dev/zero >&2\n/bin/sleep 5\n",
    )

    started = time.monotonic()
    with pytest.raises(VideoDecodeError) as caught:
        list(decode_video_chunks(source, timeout_seconds=2))

    assert caught.value.code == "invalid_frame_metadata"
    assert time.monotonic() - started < 0.5


@pytest.mark.parametrize(
    "kwargs",
    [
        {"chunk_size": 0},
        {"timeout_seconds": 0},
        {"timeout_seconds": float("nan")},
        {"max_pixels": 0},
    ],
)
def test_rejects_invalid_decode_limits(tmp_path: Path, kwargs: dict[str, float]) -> None:
    source = tmp_path / "source.mkv"
    source.touch()

    with pytest.raises(VideoDecodeError) as caught:
        list(decode_video_chunks(source, **kwargs))

    assert caught.value.code == "invalid_options"
