import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image


class Segmenter:
    model_id = "fixture-segmenter"
    model_version = "fixture-segmenter-v1"

    def __init__(self, *, fail: str | None = None):
        self.fail = fail
        self.unloaded = False

    def segment_image(self, image, prompt):
        from pipe_inspector.vision.geometry import MaskPrediction

        assert image.size == (8, 6)
        assert prompt.width == 8 and prompt.height == 6
        assert prompt.bbox_xyxy == (2, 1, 7, 5)
        if self.fail == "raise":
            raise RuntimeError("segmenter fixture failed")
        mask = np.zeros((6, 8), dtype=bool)
        mask[2:4, 3:6] = True
        if self.fail == "shape":
            mask = mask[:5]
        return MaskPrediction(mask=mask, score=0.81)

    def unload(self):
        self.unloaded = True


class Depth:
    model_id = "fixture-depth"
    model_version = "fixture-depth-v1"
    larger_is = "nearer"

    def __init__(self, *, fail: str | None = None):
        self.fail = fail
        self.unloaded = False

    def predict_depth(self, image):
        assert image.size == (8, 6)
        if self.fail == "raise":
            raise RuntimeError("depth fixture failed")
        values = np.tile(np.arange(8, dtype=np.float32), (6, 1))
        if self.fail == "constant":
            values[:] = 1
        return values

    def unload(self):
        self.unloaded = True


class Probe:
    def __init__(self, device, events):
        self.device = device
        self.events = events

    def begin(self):
        self.events.append(("probe-begin", self.device))

    def finish(self):
        self.events.append(("probe-finish", self.device))
        return {
            "status": "not_applicable",
            "peak_allocated_bytes": None,
            "synchronized": False,
        }


def _inputs(tmp_path: Path, *, suffix: str = ".jpg"):
    image = tmp_path / f"source{suffix}"
    format_name = "JPEG" if suffix.lower() in {".jpg", ".jpeg"} else "PNG"
    Image.new("RGB", (8, 6), (40, 90, 130)).save(image, format=format_name)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            [
                {
                    "sample_id": "sample-one",
                    "image": image.name,
                    "prompt_xyxy": [2, 1, 7, 5],
                    "prompt_source": "fixed existing YOLO box; not mask truth",
                }
            ]
        ),
        encoding="utf-8",
    )
    segmentation_checkpoint = tmp_path / "segmentation-checkpoint"
    depth_checkpoint = tmp_path / "depth-checkpoint"
    segmentation_checkpoint.mkdir()
    depth_checkpoint.mkdir()
    return manifest, image, segmentation_checkpoint, depth_checkpoint, tmp_path / "gallery"


def _evaluate(
    inputs,
    *,
    segmenter_factory=None,
    depth_factory=None,
    device="cpu",
):
    from pipe_inspector.evaluation.geometry import evaluate

    manifest, _, segmentation_checkpoint, depth_checkpoint, output = inputs
    events = []
    segmenters = []
    depths = []

    def make_segmenter(checkpoint, selected_device):
        events.append(("segmenter-factory", checkpoint, selected_device))
        adapter = (segmenter_factory or Segmenter)()
        segmenters.append(adapter)
        return adapter

    def make_depth(checkpoint, selected_device):
        events.append(("depth-factory", checkpoint, selected_device))
        adapter = (depth_factory or Depth)()
        depths.append(adapter)
        return adapter

    summary = evaluate(
        manifest,
        segmentation_checkpoint,
        depth_checkpoint,
        output,
        device=device,
        segmenter_factory=make_segmenter,
        depth_factory=make_depth,
        cuda_probe_factory=lambda selected_device: Probe(selected_device, events),
    )
    return summary, output, events, segmenters, depths


def test_success_preserves_original_bytes_and_writes_real_gallery_evidence(tmp_path):
    inputs = _inputs(tmp_path)
    _, source, segmentation_checkpoint, depth_checkpoint, _ = inputs
    original_bytes = source.read_bytes()

    summary, output, events, segmenters, depths = _evaluate(inputs)

    assert summary == {
        "sample_count": 1,
        "succeeded": 1,
        "partial": 0,
        "failed": 0,
    }
    assert source.read_bytes() == original_bytes
    sample_dir = output / "sample-one"
    assert (sample_dir / "original.jpg").read_bytes() == original_bytes
    for name in (
        "prompt-box.png",
        "mask.png",
        "overlay.png",
        "depth-preview.png",
        "depth.npy",
        "measurement.json",
        "result.json",
    ):
        assert (sample_dir / name).is_file(), name
    with Image.open(sample_dir / "prompt-box.png") as prompt:
        assert prompt.size == (8, 6)
        assert prompt.getpixel((2, 1)) != prompt.getpixel((0, 0))
    with Image.open(sample_dir / "mask.png") as mask:
        assert mask.mode == "L" and mask.size == (8, 6)
        assert np.count_nonzero(np.asarray(mask)) == 6
    raw_depth = np.load(sample_dir / "depth.npy", allow_pickle=False)
    assert raw_depth.dtype == np.float32 and raw_depth.shape == (6, 8)
    measurement = json.loads((sample_dir / "measurement.json").read_text())
    assert measurement["area_pixels"] == 6
    assert measurement["image_plane_fraction"] == 0.125
    assert measurement["bbox_width_pixels"] == 3
    assert measurement["bbox_height_pixels"] == 2
    assert measurement["relative_depth"] == {
        "q10": 3.0,
        "median": 4.0,
        "q90": 5.0,
        "larger_is": "nearer",
        "units": "relative_model_units",
    }
    payload = json.loads((output / "results.json").read_text())
    row = payload["samples"][0]
    assert payload["schema_version"] == "geometry-evaluation-v1"
    assert row["prompt_source"] == "fixed existing YOLO box; not mask truth"
    assert row["prompt"]["bbox_xyxy"] == [2.0, 1.0, 7.0, 5.0]
    assert row["calls"]["segmentation"]["result"]["schema_version"] == "1"
    assert row["calls"]["depth"]["result"]["schema_version"] == "1"
    assert row["calls"]["measurement"]["status"] == "succeeded"
    assert row["calls"]["source"]["gallery_path"] == "original.jpg"
    assert row["calls"]["segmentation"]["gallery_paths"] == {
        "mask": "mask.png",
        "overlay": "overlay.png",
    }
    assert row["calls"]["depth"]["gallery_paths"] == {
        "raw": "depth.npy",
        "preview": "depth-preview.png",
    }
    assert "defect_confidence" not in json.dumps(payload)
    assert "blockage" not in json.dumps(payload).lower()
    assert row["calls"]["segmentation"]["model_score_label"] == (
        "model mask quality estimate; not defect confidence"
    )
    assert row["calls"]["depth"]["preview_legend"] == "white=near, black=far"
    assert row["input_asset"]["ref"]["asset_id"]
    assert row["calls"]["segmentation"]["result"]["mask_asset"]["ref"]["asset_id"]
    assert row["calls"]["depth"]["result"]["raw_asset"]["ref"]["asset_id"]
    assert all(adapter.unloaded for adapter in [*segmenters, *depths])
    assert events == [
        ("probe-begin", "cpu"),
        ("segmenter-factory", segmentation_checkpoint, "cpu"),
        ("probe-finish", "cpu"),
        ("probe-begin", "cpu"),
        ("depth-factory", depth_checkpoint, "cpu"),
        ("probe-finish", "cpu"),
    ]
    assert "mask truth" in (output / "index.html").read_text(encoding="utf-8")


def test_segmentation_failure_keeps_depth_but_skips_measurement(tmp_path):
    inputs = _inputs(tmp_path, suffix=".png")

    summary, output, _, segmenters, depths = _evaluate(
        inputs, segmenter_factory=lambda: Segmenter(fail="raise")
    )

    assert summary == {
        "sample_count": 1,
        "succeeded": 0,
        "partial": 1,
        "failed": 0,
    }
    row = json.loads((output / "results.json").read_text())["samples"][0]
    assert row["status"] == "partial"
    assert row["calls"]["segmentation"]["status"] == "failed"
    assert row["calls"]["depth"]["status"] == "succeeded"
    assert row["calls"]["measurement"] == {
        "status": "skipped",
        "reason": "segmentation_failed",
    }
    assert not (output / "sample-one" / "mask.png").exists()
    assert not (output / "sample-one" / "measurement.json").exists()
    assert (output / "sample-one" / "depth.npy").is_file()
    assert all(adapter.unloaded for adapter in [*segmenters, *depths])
    assert "segmenter fixture failed" in (output / "sample-one" / "result.json").read_text()


def test_depth_failure_writes_mask_only_measurement_with_explicit_partial_status(tmp_path):
    inputs = _inputs(tmp_path)

    summary, output, _, segmenters, depths = _evaluate(
        inputs, depth_factory=lambda: Depth(fail="constant")
    )

    assert summary["partial"] == 1 and summary["failed"] == 0
    row = json.loads((output / "results.json").read_text())["samples"][0]
    assert row["status"] == "partial"
    assert row["calls"]["segmentation"]["status"] == "succeeded"
    assert row["calls"]["depth"]["status"] == "failed"
    assert row["calls"]["measurement"]["status"] == "partial"
    assert row["calls"]["measurement"]["reason"] == "depth_failed_mask_only"
    measurement = json.loads((output / "sample-one" / "measurement.json").read_text())
    assert measurement["area_pixels"] == 6
    assert measurement["relative_depth"] is None
    assert not (output / "sample-one" / "depth.npy").exists()
    assert all(adapter.unloaded for adapter in [*segmenters, *depths])


def test_bad_output_is_recorded_per_image_and_later_samples_continue(tmp_path):
    inputs = _inputs(tmp_path)
    manifest, source, _, _, _ = inputs
    second = tmp_path / "second.jpg"
    second.write_bytes(source.read_bytes())
    rows = json.loads(manifest.read_text())
    rows.append(rows[0] | {"sample_id": "sample-two", "image": second.name})
    manifest.write_text(json.dumps(rows), encoding="utf-8")
    calls = 0

    def segmenter_factory():
        nonlocal calls
        calls += 1
        return Segmenter(fail="shape" if calls == 1 else None)

    summary, output, _, _, _ = _evaluate(inputs, segmenter_factory=segmenter_factory)

    assert summary == {
        "sample_count": 2,
        "succeeded": 1,
        "partial": 1,
        "failed": 0,
    }
    samples = json.loads((output / "results.json").read_text())["samples"]
    assert samples[0]["calls"]["segmentation"]["status"] == "failed"
    assert samples[1]["calls"]["segmentation"]["status"] == "succeeded"
    assert not (output / "sample-one" / "mask.png").exists()
    assert (output / "sample-two" / "mask.png").is_file()


def test_invalid_source_is_visible_and_cli_returns_failure_without_fabricated_tools(tmp_path):
    from pipe_inspector.evaluation.geometry import main

    inputs = _inputs(tmp_path)
    manifest, source, segmentation_checkpoint, depth_checkpoint, output = inputs
    source.unlink()

    status = main(
        [
            "--manifest",
            str(manifest),
            "--segmentation-checkpoint",
            str(segmentation_checkpoint),
            "--depth-checkpoint",
            str(depth_checkpoint),
            "--output",
            str(output),
            "--device",
            "cpu",
        ],
        segmenter_factory=lambda *_: pytest.fail("segmenter must not be fabricated"),
        depth_factory=lambda *_: pytest.fail("depth must not be fabricated"),
    )

    assert status == 1
    row = json.loads((output / "results.json").read_text())["samples"][0]
    assert row["status"] == "failed"
    assert row["calls"]["source"]["status"] == "failed"
    assert row["calls"]["segmentation"] == {"status": "skipped", "reason": "invalid_source"}
    assert row["calls"]["depth"] == {"status": "skipped", "reason": "invalid_source"}
    assert row["calls"]["measurement"] == {
        "status": "skipped",
        "reason": "invalid_source",
    }
    assert not (output / "sample-one" / "mask.png").exists()


def test_manifest_and_checkpoint_validation_happens_before_output_and_output_is_never_reused(
    tmp_path,
):
    from pipe_inspector.evaluation.geometry import evaluate

    inputs = _inputs(tmp_path)
    manifest, _, segmentation_checkpoint, depth_checkpoint, output = inputs
    evaluate(
        manifest,
        segmentation_checkpoint,
        depth_checkpoint,
        output,
        device="cpu",
        segmenter_factory=lambda *_: Segmenter(),
        depth_factory=lambda *_: Depth(),
    )
    with pytest.raises(FileExistsError, match="new output"):
        evaluate(manifest, segmentation_checkpoint, depth_checkpoint, output, device="cpu")

    invalid_output = tmp_path / "invalid-output"
    rows = json.loads(manifest.read_text()) * 9
    for index, row in enumerate(rows):
        row["sample_id"] = f"sample-{index}"
    manifest.write_text(json.dumps(rows), encoding="utf-8")
    with pytest.raises(ValueError, match="1..8"):
        evaluate(
            manifest,
            segmentation_checkpoint,
            depth_checkpoint,
            invalid_output,
            device="cpu",
        )
    assert not invalid_output.exists()

    manifest.write_text(
        json.dumps(
            [
                {
                    "sample_id": "fixture",
                    "image": "source.jpg",
                    "prompt_xyxy": [0, 0, 1, 1],
                    "prompt_source": "fixed box",
                }
            ]
        ),
        encoding="utf-8",
    )
    missing_checkpoint = tmp_path / "missing"
    with pytest.raises(FileNotFoundError, match="checkpoint"):
        evaluate(
            manifest,
            missing_checkpoint,
            depth_checkpoint,
            invalid_output,
            device="cpu",
        )
    assert not invalid_output.exists()
