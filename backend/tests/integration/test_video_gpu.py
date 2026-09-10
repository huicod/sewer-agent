"""Opt-in real checkpoint/decoder smoke; repeated still frames are not video-effect validation."""

import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from pipe_inspector.contracts.assets import AssetRef
from pipe_inspector.vision.detector import UltralyticsDetector
from pipe_inspector.vision.video_scan import extract_scan_evidence, scan_video

pytestmark = [
    pytest.mark.integration,
    pytest.mark.gpu,
    pytest.mark.skipif(
        os.getenv("SEWER_RUN_VIDEO_GPU_TEST") != "1",
        reason="explicit opt-in: trusted checkpoint and local GPU; no API",
    ),
]


def test_existing_detector_scans_all_fixture_video_frames_on_gpu(tmp_path):
    clip = tmp_path / "trusted-still-video.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-loop",
            "1",
            "-framerate",
            "4",
            "-i",
            "var/fixtures/sewer2403-sample.jpg",
            "-frames:v",
            "4",
            "-c:v",
            "mpeg4",
            "-q:v",
            "2",
            str(clip),
        ],
        check=True,
        timeout=20,
    )
    detector = UltralyticsDetector(
        Path("yolov26/experiments/sewer2403_5class_yolo26s_img960_e100/weights/best.pt")
    )
    result = scan_video(
        clip, AssetRef(asset_id=uuid4()), detector, chunk_size=2, scratch_root=tmp_path
    )
    assert result.decode_complete and result.error_code is None
    assert result.state.next_frame == 4
    aj = [event for event in result.state.events if event.label == "AJ"]
    assert len(aj) == 1 and aj[0].observations == 4
    assert (aj[0].start_seconds, aj[0].end_seconds) == (0.0, 0.75)
    artifacts = extract_scan_evidence(clip, result, tmp_path / "frames", chunk_size=2)
    assert 1 <= len(artifacts) <= 4
    assert all((tmp_path / "frames" / frame.relative_path).is_file() for frame in artifacts)
    assert result.inspection_result == "pending"
    print(
        json.dumps(
            {
                "frames": result.state.next_frame,
                "events": [
                    {"label": e.label, "observations": e.observations} for e in result.state.events
                ],
                "evidence_frames": len(artifacts),
                "scope": "decoder_detector_integration_not_video_accuracy",
            }
        )
    )
