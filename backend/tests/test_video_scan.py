import subprocess
from contextlib import closing
from types import SimpleNamespace

import pytest
from PIL import Image
from pipe_inspector.contracts.assets import AssetRef
from pipe_inspector.contracts.detection import DetectionBatch
from pipe_inspector.vision import video_decode
from pipe_inspector.vision.video_scan import extract_scan_evidence, scan_video


@pytest.fixture
def clip(tmp_path):
    path = tmp_path / "short event.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=32x24:r=5",
            "-vf",
            "drawbox=x=0:y=0:w=iw:h=ih:color=white:t=fill:enable='eq(n,2)'",
            "-frames:v",
            "6",
            "-c:v",
            "mpeg4",
            str(path),
        ],
        check=True,
        timeout=15,
    )
    return path


class PixelDetector:
    detector_id, model_version, label_space_version = "pixel-fixture", "1", "five"

    def __init__(self, *, batch=True, fail_after=None):
        self.capabilities = SimpleNamespace(supports_batch=batch)
        self.batches, self.calls, self.fail_after = [], 0, fail_after

    def predict_image(self, path):
        if self.fail_after is not None and self.calls >= self.fail_after:
            raise RuntimeError("fixture inference failure")
        self.calls += 1
        with Image.open(path) as im:
            width, height = im.size
            bright = im.convert("RGB").getpixel((width // 2, height // 2))[0] > 200
        return DetectionBatch.model_validate(
            {
                "detector_id": self.detector_id,
                "model_version": self.model_version,
                "label_space_version": self.label_space_version,
                "width": width,
                "height": height,
                "detections": [
                    {"label": "PL", "native_label": "PL", "score": 0.7, "bbox_xyxy": [1, 1, 20, 20]}
                ]
                if bright
                else [],
            }
        )

    def predict_batch(self, paths):
        assert self.capabilities.supports_batch
        self.batches.append(len(paths))
        return tuple(self.predict_image(path) for path in paths)


def source_ref():
    return AssetRef(asset_id="22222222-2222-4222-8222-222222222222")


@pytest.mark.parametrize("batch", [True, False])
def test_real_decode_scans_every_frame_and_preserves_single_frame_event(clip, tmp_path, batch):
    detector = PixelDetector(batch=batch)
    chunks = []
    result = scan_video(
        clip,
        source_ref(),
        detector,
        chunk_size=2,
        scratch_root=tmp_path,
        on_chunk=lambda observations, state: chunks.append((observations, state)),
    )
    assert result.decode_complete and result.error_code is None
    assert result.state.next_frame == detector.calls == 6
    assert len(chunks) == 3
    assert len(result.state.events) == 1
    event = result.state.events[0]
    assert (event.start_frame, event.end_frame) == (2, 2)
    assert event.start_seconds == pytest.approx(0.4)
    assert detector.batches == ([2, 2, 2] if batch else [])
    assert result.inspection_result == "pending"
    evidence = extract_scan_evidence(clip, result, tmp_path / "evidence", chunk_size=2)
    assert {item.frame_index for item in evidence} == {0, 2}
    assert detector.calls == 6  # evidence extraction decodes only, does not infer
    event_frame = next(item for item in evidence if item.frame_index == 2)
    with Image.open(tmp_path / "evidence" / event_frame.relative_path) as im:
        assert im.convert("RGB").getpixel((16, 12))[0] > 200


def test_detector_failure_retains_prior_chunks_and_reports_partial(clip, tmp_path):
    detector = PixelDetector(fail_after=4)
    result = scan_video(clip, source_ref(), detector, chunk_size=2, scratch_root=tmp_path)
    assert not result.decode_complete and result.error_code == "detection_failed"
    assert result.state.next_frame == 4
    assert result.state.events[0].start_frame == 2
    assert result.inspection_result == "pending"


def test_decode_failure_cannot_produce_complete_coverage(tmp_path):
    result = scan_video(
        tmp_path / "missing.mp4", source_ref(), PixelDetector(), scratch_root=tmp_path
    )
    assert not result.decode_complete and result.error_code
    assert result.state.next_frame == 0
    assert result.inspection_result == "pending"


def test_decode_error_preserves_complete_frames_in_unfilled_chunk(clip, tmp_path, monkeypatch):
    original_frames = video_decode._raw_frames

    def broken_tail(process, **options):
        with closing(original_frames(process, **options)) as frames:
            for index, frame in enumerate(frames):
                yield frame
                if index == 2:
                    raise video_decode.VideoDecodeError("decode_failed")

    monkeypatch.setattr(video_decode, "_raw_frames", broken_tail)
    detector = PixelDetector()
    result = scan_video(clip, source_ref(), detector, chunk_size=8, scratch_root=tmp_path)
    assert result.state.next_frame == detector.calls == 3
    assert result.state.events[0].start_frame == 2
    assert not result.decode_complete and result.error_code == "decode_failed"
    assert result.inspection_result == "pending"


def test_wrong_batch_count_is_not_silently_zipped_away(clip, tmp_path):
    detector = PixelDetector()
    detector.predict_batch = lambda paths: ()
    result = scan_video(clip, source_ref(), detector, chunk_size=2, scratch_root=tmp_path)
    assert result.error_code == "invalid_detection_batch"
    assert not result.decode_complete and result.state.next_frame == 0


def test_checkpoint_write_error_is_not_hidden_as_success(clip, tmp_path):
    def fail_write(*args):
        raise OSError("fixture disk failure")

    with pytest.raises(OSError):
        scan_video(clip, source_ref(), PixelDetector(), scratch_root=tmp_path, on_chunk=fail_write)


def test_resume_does_not_infer_committed_prefix_and_keeps_original_event(clip, tmp_path):
    saved = []
    original = scan_video(
        clip,
        source_ref(),
        PixelDetector(),
        chunk_size=2,
        on_chunk=lambda observations, state: saved.append(state),
    )
    detector = PixelDetector()
    decoded = []
    resumed = scan_video(
        clip,
        source_ref(),
        detector,
        chunk_size=2,
        initial_state=saved[0],
        on_progress=lambda index, timestamp: decoded.append(index),
    )
    assert detector.calls == 4
    assert decoded == [0, 1, 2, 3, 4, 5]
    assert resumed == original


def test_scan_uses_durable_winning_cursor_returned_by_commit(clip):
    saved = []
    scan_video(
        clip,
        source_ref(),
        PixelDetector(),
        chunk_size=2,
        on_chunk=lambda observations, state: saved.append(state),
    )
    detector = PixelDetector()

    def concurrent_commit(observations, state):
        return saved[1] if observations[0].frame_index == 0 else state

    result = scan_video(clip, source_ref(), detector, chunk_size=2, on_chunk=concurrent_commit)
    assert result.decode_complete and result.state.next_frame == 6
    assert detector.calls == 4  # 0,1 inferred here; 2,3 already committed by the winning attempt
    assert result.state.events[0].start_frame == 2


def test_resume_rejects_changed_source_and_model_before_inference(clip):
    saved = []
    scan_video(
        clip,
        source_ref(),
        PixelDetector(),
        chunk_size=2,
        on_chunk=lambda observations, state: saved.append(state),
    )
    detector = PixelDetector()
    with pytest.raises(ValueError, match="source"):
        scan_video(
            clip,
            AssetRef(asset_id="33333333-3333-4333-8333-333333333333"),
            detector,
            initial_state=saved[0],
        )
    detector.model_version = "changed-model"
    with pytest.raises(ValueError, match="metadata"):
        scan_video(clip, source_ref(), detector, initial_state=saved[0])
    assert detector.calls == 0
