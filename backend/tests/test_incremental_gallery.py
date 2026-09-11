import base64
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image
from pipe_inspector.evaluation.incremental_gallery import export_incremental_gallery

SVG = {"svg": "http://www.w3.org/2000/svg"}


def _image(path: Path, colour: tuple[int, int, int] = (25, 50, 75)) -> bytes:
    Image.new("RGB", (100, 80), colour).save(path, format="PNG")
    return path.read_bytes()


def _box(label: str, bbox: list[int], *, score: float = 0.8) -> dict:
    return {"label": label, "bbox_xyxy": bbox, "score": score}


def _observation(boxes: list[dict]) -> dict:
    return {
        "detection": {
            "detector_id": "primary-fixture",
            "model_version": "primary-v1",
            "label_space_version": "sewer2403-5class-v1",
            "width": 100,
            "height": 80,
            "detections": [{**box, "native_label": box["label"]} for box in boxes],
        },
        "quality": {
            "mean_luminance": 90,
            "contrast": 30,
            "sharpness": 50,
            "adequate": True,
            "reasons": [],
        },
    }


def _review(*, summary: str = "full image reviewed", name: str = "side opening") -> dict:
    return {
        "image_assessable": True,
        "full_image_audited": True,
        "candidates": [],
        "additional_anomalies": [
            {
                "label": "AJ",
                "name": name,
                "bbox_xyxy": [55, 15, 75, 35],
                "reason": "localized suggestion",
            }
        ],
        "summary": summary,
    }


def _sample(
    tmp_path: Path,
    sample_id: str = "image-one",
    *,
    source_kind: str = "image",
    ground_truth: list[dict] | None = None,
) -> dict:
    path = tmp_path / f"source-{len(list(tmp_path.glob('source-*.png')))}.png"
    _image(path)
    primary = [_box("BX", [10, 10, 40, 40], score=0.91)]
    return {
        "sample_id": sample_id,
        "group_id": "shared-video" if source_kind == "video" else f"group-{sample_id}",
        "image_path": str(path.resolve()),
        "width": 100,
        "height": 80,
        "source_kind": source_kind,
        "ground_truth": (
            [_box("FS", [12, 12, 38, 38])]
            if ground_truth is None and source_kind == "image"
            else ground_truth
        ),
        "frame_index": 7 if source_kind == "video" else None,
        "timestamp_seconds": 0.28 if source_kind == "video" else None,
        "baseline_policy": (
            "saved-video-review-v1" if source_kind == "video" else "guided-confidence-v2"
        ),
        "source_pins": {"detector": "primary-v1"},
        "observation": _observation(primary),
        "review": _review(),
        "review_error": None,
        "baseline_predictions": [_box("BX", [10, 10, 40, 40], score=0.91)],
        "changes": [],
    }


def _record(sample: dict, *, open_status: str = "succeeded") -> dict:
    primary = [_box("BX", [10, 10, 40, 40], score=0.91)]
    guided = [*primary, _box("AJ", [55, 15, 75, 35], score=1.0)]
    open_candidates = [
        {
            "label": "BLOCK",
            "name": "debris pile",
            "score": 0.72,
            "bbox_xyxy": [60, 45, 90, 70],
        }
    ]
    return {
        "sample_id": sample["sample_id"],
        "observation": sample["observation"],
        "review": sample["review"],
        "review_error": None,
        "changes": sample["changes"],
        "primary": primary,
        "guided": guided,
        "combined": [*guided, _box("BLOCK", [60, 45, 90, 70], score=0.72)],
        "open_batch": {
            "detector_id": "open-fixture",
            "model_version": "open-v1",
            "vocabulary_version": "sewer-open-v1",
            "width": 100,
            "height": 80,
            "candidates": open_candidates,
        }
        if open_status == "succeeded"
        else None,
        "open_added": open_candidates if open_status == "succeeded" else [],
        "open_status": open_status,
        "open_error": None,
        "trigger": "suspected_miss" if open_status != "skipped" else None,
        "open_duration_seconds": 0.125,
    }


def _svg_box_labels(path: Path) -> list[str]:
    root = ET.parse(path).getroot()
    return [item.attrib["data-label"] for item in root.findall("svg:rect[@data-box-index]", SVG)]


def test_exports_exact_source_pixels_and_every_image_stage(tmp_path):
    sample = _sample(tmp_path)
    record = _record(sample)
    original = Path(sample["image_path"]).read_bytes()

    index_path = export_incremental_gallery([sample], [record], tmp_path / "gallery")

    directory = index_path.parent / "sample-0001"
    assert index_path == tmp_path / "gallery" / "index.html"
    assert (directory / "original.png").read_bytes() == original
    expected_layers = {
        "primary.svg",
        "vlm-suggestions.svg",
        "baseline-guided.svg",
        "open-candidates.svg",
        "combined-tentative.svg",
        "original-annotations.svg",
    }
    assert expected_layers <= {path.name for path in directory.iterdir()}
    for name in expected_layers:
        root = ET.parse(directory / name).getroot()
        embedded = root.find("svg:image", SVG)
        assert embedded is not None
        href = embedded.attrib["href"]
        assert href.startswith("data:image/png;base64,")
        assert base64.b64decode(href.partition(",")[2]) == original

    assert _svg_box_labels(directory / "primary.svg") == ["BX"]
    assert _svg_box_labels(directory / "vlm-suggestions.svg") == ["AJ"]
    assert _svg_box_labels(directory / "baseline-guided.svg") == ["BX", "AJ"]
    assert _svg_box_labels(directory / "combined-tentative.svg") == ["BX", "AJ", "BLOCK"]
    index = index_path.read_text(encoding="utf-8")
    detail = (directory / "index.html").read_text(encoding="utf-8")
    assert "sample-0001/index.html" in index
    assert "sample-0001/evidence.json" in index
    assert "Source pins:" in detail
    assert "primary-v1" in detail
    for name in expected_layers:
        assert f'href="{name}"' in detail
    assert "combined is a pending review queue" in detail


def test_open_layer_distinguishes_all_candidates_from_actually_added_other(tmp_path):
    sample = _sample(tmp_path)
    record = _record(sample)
    record["open_batch"]["candidates"] = [
        {
            "label": "OTHER",
            "name": "roots & unknown",
            "score": 0.64,
            "bbox_xyxy": [42, 42, 70, 72],
        },
        {
            "label": "AJ",
            "name": "duplicate opening",
            "score": 0.51,
            "bbox_xyxy": [55, 15, 75, 35],
        },
    ]
    record["open_added"] = [record["open_batch"]["candidates"][0]]
    record["combined"] = record["guided"]

    export_incremental_gallery([sample], [record], tmp_path / "gallery")

    directory = tmp_path / "gallery" / "sample-0001"
    root = ET.parse(directory / "open-candidates.svg").getroot()
    candidates = root.findall("svg:rect[@data-box-index]", SVG)
    assert [(item.attrib["data-label"], item.attrib["data-status"]) for item in candidates] == [
        ("OTHER", "actually-added"),
        ("AJ", "not-added"),
    ]
    assert "OTHER" not in _svg_box_labels(directory / "combined-tentative.svg")
    evidence = json.loads((directory / "evidence.json").read_text(encoding="utf-8"))
    assert evidence["open_status"] == "succeeded"
    assert evidence["open_candidates"] == [
        {
            "actually_added": True,
            "bbox_xyxy": [42.0, 42.0, 70.0, 72.0],
            "label": "OTHER",
            "name": "roots & unknown",
            "score": 0.64,
        },
        {
            "actually_added": False,
            "bbox_xyxy": [55.0, 15.0, 75.0, 35.0],
            "label": "AJ",
            "name": "duplicate opening",
            "score": 0.51,
        },
    ]
    detail = (directory / "index.html").read_text(encoding="utf-8")
    assert "all candidates: 2" in detail
    assert "actually added: 1" in detail
    assert "OTHER remains pending human review and is excluded from five-class combined" in detail


def test_unannotated_video_is_unknown_and_explains_different_baseline_policy(tmp_path):
    sample = _sample(tmp_path, "video-frame", source_kind="video", ground_truth=None)
    record = _record(sample, open_status="skipped")

    export_incremental_gallery([sample], [record], tmp_path / "gallery")

    directory = tmp_path / "gallery" / "sample-0001"
    detail = (directory / "index.html").read_text(encoding="utf-8")
    assert "Ground truth: UNKNOWN" in detail
    assert "accuracy is not computed" in detail
    assert "saved-video-review-v1" in detail
    assert "differs from image guided-confidence-v2" in detail
    assert "frame 7" in detail
    assert "0.28 seconds" in detail
    assert "SKIPPED" in detail
    assert not (directory / "original-annotations.svg").exists()
    assert not (directory / "open-candidates.svg").exists()


def test_extensionless_png_source_keeps_pixels_and_uses_browser_image_mime(tmp_path):
    sample = _sample(tmp_path, "extensionless-frame", source_kind="video", ground_truth=None)
    source = Path(sample["image_path"])
    extensionless = source.with_suffix("")
    source.rename(extensionless)
    sample["image_path"] = str(extensionless.resolve())
    original = extensionless.read_bytes()

    export_incremental_gallery(
        [sample], [_record(sample, open_status="skipped")], tmp_path / "gallery"
    )

    directory = tmp_path / "gallery" / "sample-0001"
    assert (directory / "original.png").read_bytes() == original
    root = ET.parse(directory / "primary.svg").getroot()
    image = root.find("svg:image", SVG)
    assert image is not None
    assert image.attrib["href"].startswith("data:image/png;base64,")
    assert base64.b64decode(image.attrib["href"].partition(",")[2]) == original


def test_corrupt_or_mislabeled_source_is_not_falsely_available(tmp_path):
    sample = _sample(tmp_path)
    original_path = Path(sample["image_path"])
    renamed = original_path.with_suffix(".jpg")
    original_path.rename(renamed)
    sample["image_path"] = str(renamed)
    output = tmp_path / "gallery"
    export_incremental_gallery([sample], [_record(sample)], output)
    assert (output / "sample-0001/original.png").exists()
    renamed.write_bytes(b"not an image")
    export_incremental_gallery([sample], [_record(sample)], output)
    assert 'data-source="unavailable"' in (output / "sample-0001/index.html").read_text()


def test_failed_scan_missing_record_and_missing_source_are_never_empty_success(tmp_path):
    failed = _sample(tmp_path, "failed")
    missing = _sample(tmp_path, "missing")
    unavailable = _sample(tmp_path, "unavailable")
    Path(unavailable["image_path"]).unlink()
    failed_record = _record(failed, open_status="failed")
    failed_record["open_error"] = "open_scan_failed"
    failed_record["review"] = None
    failed_record["review_error"] = "vlm_replay_failed"
    unavailable_record = _record(unavailable, open_status="failed")
    unavailable_record["open_error"] = "source_unavailable"

    index_path = export_incremental_gallery(
        [failed, missing, unavailable],
        [failed_record, unavailable_record],
        tmp_path / "gallery",
    )

    index = index_path.read_text(encoding="utf-8")
    assert 'data-expected="3" data-recorded="2" data-missing="1"' in index
    failed_dir = index_path.parent / "sample-0001"
    failed_detail = (failed_dir / "index.html").read_text(encoding="utf-8")
    assert "FAILED" in failed_detail
    assert "open_scan_failed" in failed_detail
    assert "VLM review unavailable: vlm_replay_failed" in failed_detail
    assert not (failed_dir / "vlm-suggestions.svg").exists()
    assert not (failed_dir / "open-candidates.svg").exists()

    missing_dir = index_path.parent / "sample-0002"
    missing_detail = (missing_dir / "index.html").read_text(encoding="utf-8")
    assert "incremental record missing" in missing_detail
    assert (missing_dir / "primary.svg").exists()
    assert (missing_dir / "baseline-guided.svg").exists()
    assert not (missing_dir / "open-candidates.svg").exists()
    assert not (missing_dir / "combined-tentative.svg").exists()

    unavailable_dir = index_path.parent / "sample-0003"
    unavailable_detail = (unavailable_dir / "index.html").read_text(encoding="utf-8")
    assert "original file unavailable" in unavailable_detail
    assert "source_unavailable" in unavailable_detail
    assert (unavailable_dir / "original-unavailable.svg").exists()


def test_ids_candidate_names_and_errors_are_html_and_svg_escaped(tmp_path):
    hostile_id = '<img src=x onerror="boom()">../../escape'
    sample = _sample(tmp_path, hostile_id)
    sample["group_id"] = "<script>group()</script>"
    sample["review"] = _review(
        summary="</textarea><script>summary()</script>",
        name='<svg onload="suggestion()">',
    )
    record = _record(sample)
    record["review"] = sample["review"]
    record["open_status"] = "failed"
    record["open_batch"] = None
    record["open_added"] = []
    record["open_error"] = "<b>provider failed</b>"

    export_incremental_gallery([sample], [record], tmp_path / "gallery")

    directory = tmp_path / "gallery" / "sample-0001"
    index = (tmp_path / "gallery" / "index.html").read_text(encoding="utf-8")
    detail = (directory / "index.html").read_text(encoding="utf-8")
    suggestions = (directory / "vlm-suggestions.svg").read_text(encoding="utf-8")
    markup = index + detail + suggestions
    assert '<img src=x onerror="boom()">' not in markup
    assert "<script>group()" not in markup
    assert "<script>summary()" not in markup
    assert "<svg onload=" not in markup
    assert "<b>provider failed</b>" not in markup
    assert "&lt;img src=x onerror=&quot;boom()&quot;&gt;../../escape" in markup
    assert "&lt;b&gt;provider failed&lt;/b&gt;" in detail
    assert not (tmp_path / "escape").exists()
