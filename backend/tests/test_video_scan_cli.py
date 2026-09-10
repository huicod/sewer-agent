import json
import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_video_scan import PixelDetector, clip  # noqa: F401 - real FFmpeg fixture


def script():
    return runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/video_scan.py"))


def args(source, output):
    return SimpleNamespace(
        source=source,
        output=output,
        checkpoint=Path("fixture.pt"),
        device="cpu",
        imgsz=960,
        conf=0.25,
        chunk_size=2,
        timeout_seconds=10,
        max_pixels=100_000,
    )


def test_cli_artifacts_preserve_all_frame_records_and_selected_evidence(clip, tmp_path):  # noqa: F811
    output = tmp_path / "scan"
    result = script()["run_scan"](args(clip, output), PixelDetector())
    saved = json.loads((output / "manifest.json").read_text())
    assert saved == result
    rows = [json.loads(line) for line in (output / "observations.jsonl").read_text().splitlines()]
    assert [row["frame_index"] for row in rows] == [0, 1, 2, 3, 4, 5]
    assert saved["scan"]["state"]["next_frame"] == 6
    assert saved["scan"]["inspection_result"] == "pending"
    assert saved["scope"] == "primary_scan_only"
    assert len(saved["evidence"]) == 2
    assert all((output / "frames" / item["relative_path"]).is_file() for item in saved["evidence"])
    with pytest.raises(FileExistsError):
        script()["run_scan"](args(clip, output), PixelDetector())


def test_cli_keeps_partial_output_without_overstating_coverage(clip, tmp_path):  # noqa: F811
    output = tmp_path / "partial"
    result = script()["run_scan"](args(clip, output), PixelDetector(fail_after=4))
    assert result["scan"]["error_code"] == "detection_failed"
    assert not result["scan"]["decode_complete"]
    assert len((output / "observations.jsonl").read_text().splitlines()) == 4
