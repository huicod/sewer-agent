from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "yolov26/code/prepare_sewer2403_yolo.py"


def load_prepare_module():
    spec = importlib.util.spec_from_file_location("test_sewer2403_prepare", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_video_grouping_collapses_explicit_frame_suffixes_only() -> None:
    prepare = load_prepare_module()

    family = "video:01a-3056-901a-3052-01"
    assert (
        prepare.group_key(
            Path("01A-3056-901A-3052-01_frame10367_png.rf.eb8c6bb185aa6457137d891e34a83066.jpg"),
            "video",
        )
        == family
    )
    assert prepare.group_key(Path("01A-3056-901A-3052-01_FRAME7.png"), "video") == family
    assert (
        prepare.group_key(Path("01A-3056-901A-3052-01_frame7_extra.png"), "video")
        == "source:01a-3056-901a-3052-01_frame7_extra"
    )


def test_explicit_frame_grouping_does_not_change_source_mode_or_mp4_rule() -> None:
    prepare = load_prepare_module()

    frame = Path("pipe-01_frame42.png")
    assert prepare.group_key(frame, "source") == "source:pipe-01_frame42"
    assert (
        prepare.group_key(Path("01-00510-mp4_000224-354.jpg"), "video")
        == "video:01-00510-mp4"
    )
