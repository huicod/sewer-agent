import json
from collections import Counter
from pathlib import Path

import pytest
from PIL import Image
from pipe_inspector.contracts.detection import Detection, DetectionBatch
from pipe_inspector.evaluation import restoration
from pipe_inspector.evaluation.restoration import (
    compare_box_sets,
    evaluate,
    main,
)
from pipe_inspector.evaluation.review import LabeledBox

KINDS = ("dehaze", "deblur", "super_resolution")


def _models(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    kinds = {}
    rows = []
    for kind in KINDS:
        for suffix in ("existing", "candidate"):
            model_id = f"{kind}-{suffix}"
            checkpoint = tmp_path / f"{model_id}.pth"
            checkpoint.write_bytes(b"local fixture")
            rows.append({"model_id": model_id, "kind": kind, "checkpoint": str(checkpoint)})
            kinds[model_id] = kind
    path = tmp_path / "models.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path, kinds


def _sample_manifest(
    tmp_path: Path,
    *,
    size: tuple[int, int] = (2000, 1000),
    roi: tuple[int, int, int, int] = (100, 200, 300, 300),
) -> tuple[Path, Path]:
    source = tmp_path / "source.jpg"
    Image.new("RGB", size, (40, 90, 150)).save(source, format="JPEG", quality=91)
    path = tmp_path / "samples.json"
    path.write_text(
        json.dumps(
            [
                {
                    "sample_id": "sample-one",
                    "image": str(source),
                    "roi": list(roi),
                    "ground_truth": [
                        {"label": "PL", "bbox_xyxy": [200, 100, 400, 200], "score": 1.0}
                    ],
                    "reason": "fixed mapping fixture",
                }
            ]
        ),
        encoding="utf-8",
    )
    return path, source


class FakeAdapter:
    def __init__(
        self,
        model_id: str,
        kind: str,
        events: list[str],
        *,
        bad_size: bool = False,
        bad_return: bool = False,
        fail: bool = False,
        metadata_scale: int | None = None,
        unload_fail: bool = False,
    ) -> None:
        self.model_id = model_id
        self.kind = kind
        self.model_version = f"{model_id}-fixture-v1"
        self.scale = metadata_scale if metadata_scale is not None else (
            4 if kind == "super_resolution" else 1
        )
        self._events = events
        self._bad_size = bad_size
        self._bad_return = bad_return
        self._fail = fail
        self._unload_fail = unload_fail

    def enhance_image(self, image: Image.Image) -> Image.Image:
        self._events.append(f"enhance:{self.model_id}")
        if self._fail:
            raise RuntimeError(f"real failure from {self.model_id}")
        if self._bad_return:
            return 42
        scale = self.scale
        if self._bad_size:
            scale = 2
        return image.resize((image.width * scale, image.height * scale), Image.Resampling.NEAREST)

    def unload(self) -> None:
        self._events.append(f"unload:{self.model_id}")
        if self._unload_fail:
            raise RuntimeError(f"cannot unload {self.model_id}")


class ProportionalDetector:
    def __init__(
        self,
        events: list[str],
        *,
        wrong_dimensions: bool = False,
        unload_fail: bool = False,
    ) -> None:
        self.events = events
        self.wrong_dimensions = wrong_dimensions
        self.unload_fail = unload_fail

    def predict_image(self, path: str | Path) -> DetectionBatch:
        path = Path(path)
        self.events.append(f"detect:{path.name}")
        with Image.open(path) as image:
            width, height = image.size
        return DetectionBatch(
            detector_id="fixture-detector",
            model_version="fixture-detector-v1",
            label_space_version="sewer2403-5class-v1",
            width=width + int(self.wrong_dimensions),
            height=height,
            detections=(
                Detection(
                    label="PL",
                    native_label="PL",
                    score=0.9,
                    bbox_xyxy=(width * 0.1, height * 0.1, width * 0.2, height * 0.2),
                ),
            ),
        )

    def unload(self) -> None:
        self.events.append("detector-unload")
        if self.unload_fail:
            raise RuntimeError("detector unload failed")


class FakeCudaProbe:
    def __init__(self) -> None:
        self.events = []

    def begin(self) -> None:
        self.events.append("begin")

    def finish(self) -> dict:
        self.events.append("finish")
        return {
            "status": "available",
            "peak_allocated_bytes": 123_456,
            "synchronized": True,
        }


def _run(
    tmp_path: Path,
    *,
    adapter_options: dict[str, dict] | None = None,
    detector: ProportionalDetector | None = None,
) -> tuple[dict, Path, list[str], dict[str, str]]:
    samples, _ = _sample_manifest(tmp_path)
    models, kinds = _models(tmp_path)
    output = tmp_path / "evaluation"
    events: list[str] = []
    options = adapter_options or {}

    def model_factory(model_id: str, checkpoint: Path, device: str):
        assert checkpoint.is_file()
        assert device == "cpu"
        return FakeAdapter(model_id, kinds[model_id], events, **options.get(model_id, {}))

    result = evaluate(
        samples,
        models,
        output,
        detector_checkpoint=tmp_path / "detector.pt",
        device="cpu",
        model_factory=model_factory,
        detector_factory=(lambda **kwargs: detector or ProportionalDetector(events)),
    )
    return result, output, events, kinds


def _box(label: str, xyxy: tuple[int, int, int, int], score: float) -> LabeledBox:
    return LabeledBox(label=label, bbox_xyxy=xyxy, score=score)


def test_compare_box_sets_is_class_aware_one_to_one_and_tracks_strong_baselines() -> None:
    baseline = [
        _box("PL", (0, 0, 10, 10), 0.91),
        _box("BX", (20, 20, 30, 30), 0.86),
    ]
    candidate = [
        _box("PL", (0, 0, 10, 10), 0.80),
        _box("PL", (0, 0, 10, 10), 0.70),
        _box("FS", (20, 20, 30, 30), 0.95),
    ]

    result = compare_box_sets(baseline, candidate)

    assert result["retained_baseline_indices"] == [0]
    assert result["lost_baseline_indices"] == [1]
    assert result["new_candidate_indices"] == [1, 2]
    assert result["strong_baseline"] == {
        "threshold": 0.85,
        "indices": [0, 1],
        "retained_indices": [0],
        "lost_indices": [1],
        "retention": 0.5,
    }
    assert result["matches"] == [{"baseline_index": 0, "candidate_index": 0, "iou": 1.0}]
    assert "fp" not in json.dumps(result).lower()
    assert "fn" not in json.dumps(result).lower()


def test_compare_box_sets_maximizes_cardinality_instead_of_greedy_iou() -> None:
    baseline = [
        _box("PL", (0, 0, 10, 10), 0.90),
        _box("PL", (4, 0, 14, 10), 0.80),
    ]
    candidate = [
        _box("PL", (2, 0, 12, 10), 0.95),
        _box("PL", (0, 0, 9, 10), 0.80),
    ]

    result = compare_box_sets(baseline, candidate)

    assert result["retained_baseline_indices"] == [0, 1]
    assert result["lost_baseline_indices"] == []
    assert result["new_candidate_indices"] == []
    assert {(item["baseline_index"], item["candidate_index"]) for item in result["matches"]} == {
        (0, 1),
        (1, 0),
    }


def test_compare_box_sets_prefers_higher_score_baseline_when_only_one_can_be_retained() -> None:
    baseline = [
        _box("PL", (0, 0, 10, 10), 0.60),
        _box("PL", (2, 0, 12, 10), 0.92),
    ]
    candidate = [_box("PL", (0, 0, 10, 10), 0.80)]

    result = compare_box_sets(baseline, candidate)

    assert result["retained_baseline_indices"] == [1]
    assert result["lost_baseline_indices"] == [0]
    assert result["strong_baseline"]["retained_indices"] == [1]


def test_evaluate_preserves_source_builds_controls_and_maps_every_coordinate_space(
    tmp_path,
) -> None:
    samples, source = _sample_manifest(tmp_path)
    original_bytes = source.read_bytes()
    models, kinds = _models(tmp_path)
    output = tmp_path / "evaluation"
    events: list[str] = []

    result = evaluate(
        samples,
        models,
        output,
        detector_checkpoint=tmp_path / "detector.pt",
        device="cpu",
        model_factory=lambda model_id, checkpoint, device: FakeAdapter(
            model_id, kinds[model_id], events
        ),
        detector_factory=lambda **kwargs: ProportionalDetector(events),
    )

    sample = result["samples"][0]
    assert source.read_bytes() == original_bytes
    assert (output / "sample-one" / "full-original.jpg").read_bytes() == original_bytes
    assert sample["analysis"] == {
        "original_size": [2000, 1000],
        "fullframe_size": [960, 480],
        "fullframe_to_original": [pytest.approx(2000 / 960), pytest.approx(1000 / 480)],
        "roi_original_xyxy": [100, 200, 300, 300],
        "roi_native_size": [200, 100],
        "sr_crop_coordinate_space": "original_pixels",
    }
    expected_sizes = {
        "control-fullframe.png": (960, 480),
        "control-roi-native.png": (200, 100),
        "control-roi-bicubic-x4.png": (800, 400),
    }
    for filename, expected in expected_sizes.items():
        with Image.open(output / "sample-one" / filename) as image:
            assert image.size == expected

    controls = sample["controls"]
    assert controls["fullframe"]["mapped_detections"][0]["bbox_xyxy"] == pytest.approx(
        [200, 100, 400, 200]
    )
    assert controls["roi_native"]["mapped_detections"][0]["bbox_xyxy"] == pytest.approx(
        [120, 210, 140, 220]
    )
    assert controls["roi_bicubic_x4"]["mapped_detections"][0]["bbox_xyxy"] == pytest.approx(
        [120, 210, 140, 220]
    )
    sr = sample["models"]["super_resolution-existing"]["artifact"]
    assert sr["mapped_detections"][0]["bbox_xyxy"] == pytest.approx([120, 210, 140, 220])
    assert "ground_truth_score" not in controls["roi_native"]
    assert "ground_truth_score" not in controls["roi_bicubic_x4"]
    assert "ground_truth_score" not in sr
    assert controls["fullframe"]["ground_truth_score"]["micro"]["tp"] == 1
    assert sample["models"]["dehaze-existing"]["artifact"]["ground_truth_score"]["micro"][
        "tp"
    ] == 1
    for artifact in [*controls.values(), *(job["artifact"] for job in sample["models"].values())]:
        assert (output / artifact["overlay"]).is_file()
        assert (output / artifact["detections_json"]).is_file()
    assert (output / "index.html").is_file()
    assert (output / "sample-one" / "index.html").is_file()
    detail_html = (output / "sample-one" / "index.html").read_text(encoding="utf-8")
    assert "dehaze-existing" in detail_html
    assert "耗时" in detail_html

    first_detect = next(index for index, event in enumerate(events) if event.startswith("detect:"))
    assert sum(event.startswith("unload:") for event in events[:first_detect]) == 6
    unloaded = Counter(
        event.split(":", 1)[1] for event in events if event.startswith("unload:")
    )
    assert unloaded == Counter(kinds.keys())


def test_bicubic_control_is_real_bicubic_and_compared_to_native_roi(tmp_path) -> None:
    samples, source = _sample_manifest(tmp_path, size=(8, 8), roi=(1, 1, 5, 5))
    pixels = Image.new("RGB", (8, 8))
    pixels.putdata([(x * 20, y * 20, (x + y) * 10) for y in range(8) for x in range(8)])
    pixels.save(source, format="PNG")
    samples.write_text(
        json.dumps(
            [
                {
                    "sample_id": "sample-one",
                    "image": str(source),
                    "roi": [1, 1, 5, 5],
                    "ground_truth": [],
                    "reason": "interpolation fixture",
                }
            ]
        ),
        encoding="utf-8",
    )
    models, kinds = _models(tmp_path)
    output = tmp_path / "evaluation"
    evaluate(
        samples,
        models,
        output,
        detector_checkpoint=tmp_path / "detector.pt",
        device="cpu",
        model_factory=lambda model_id, checkpoint, device: FakeAdapter(
            model_id, kinds[model_id], []
        ),
        detector_factory=lambda **kwargs: ProportionalDetector([]),
    )
    with Image.open(source) as image:
        expected = image.convert("RGB").crop((1, 1, 5, 5)).resize(
            (16, 16), Image.Resampling.BICUBIC
        )
    with Image.open(output / "sample-one" / "control-roi-bicubic-x4.png") as actual:
        assert actual.tobytes() == expected.tobytes()
    result = json.loads((output / "results.json").read_text(encoding="utf-8"))
    bicubic = result["samples"][0]["controls"]["roi_bicubic_x4"]
    assert bicubic["comparison_baseline"] == "roi_native"
    assert bicubic["baseline_comparison"]["retained_baseline_indices"] == [0]


def test_model_failure_and_wrong_size_are_visible_without_fabricated_outputs(tmp_path) -> None:
    result, output, _, _ = _run(
        tmp_path,
        adapter_options={
            "deblur-candidate": {"fail": True},
            "super_resolution-candidate": {"bad_size": True},
        },
    )

    jobs = result["samples"][0]["models"]
    assert jobs["deblur-candidate"]["status"] == "failed"
    assert jobs["deblur-candidate"]["error"] == {
        "type": "RuntimeError",
        "message": "real failure from deblur-candidate",
    }
    assert jobs["super_resolution-candidate"]["status"] == "failed"
    assert jobs["super_resolution-candidate"]["error"]["type"] == "ValueError"
    assert "expected 800x400" in jobs["super_resolution-candidate"]["error"]["message"]
    assert not (output / "sample-one" / "model-deblur-candidate.png").exists()
    assert not (output / "sample-one" / "model-super_resolution-candidate.png").exists()
    html = (output / "sample-one" / "index.html").read_text(encoding="utf-8")
    assert "real failure from deblur-candidate" in html
    assert "失败耗时" in html
    assert "model-deblur-candidate.png" not in html


def test_non_image_model_return_is_recorded_without_aborting_later_models(tmp_path) -> None:
    result, output, events, _ = _run(
        tmp_path,
        adapter_options={"dehaze-existing": {"bad_return": True}},
    )

    jobs = result["samples"][0]["models"]
    assert jobs["dehaze-existing"]["status"] == "failed"
    assert jobs["dehaze-existing"]["error"] == {
        "type": "ValueError",
        "message": "model output must be a PIL image",
    }
    assert jobs["dehaze-candidate"]["status"] == "succeeded"
    assert (output / "sample-one" / "model-dehaze-candidate.png").is_file()
    assert events.count("unload:dehaze-existing") == 1


def test_each_model_image_records_synchronized_timing_and_cuda_peak(tmp_path) -> None:
    samples, _ = _sample_manifest(tmp_path)
    models, kinds = _models(tmp_path)
    output = tmp_path / "evaluation"
    probe = FakeCudaProbe()

    result = evaluate(
        samples,
        models,
        output,
        detector_checkpoint=tmp_path / "detector.pt",
        device="cuda:0",
        model_factory=lambda model_id, checkpoint, device: FakeAdapter(
            model_id, kinds[model_id], []
        ),
        detector_factory=lambda **kwargs: ProportionalDetector([]),
        cuda_probe_factory=lambda device: probe,
    )

    job = result["samples"][0]["models"]["deblur-existing"]
    assert job["timing"] == {
        "duration_seconds": pytest.approx(job["duration_seconds"]),
        "scope": "control_png_decode+adapter_enhance+output_validation+png_encode",
        "first_adapter_invocation": True,
        "cuda_synchronized": True,
    }
    assert job["cuda_peak_allocated_bytes"] == 123_456
    assert job["cuda_measurement_status"] == "available"
    assert probe.events == ["begin", "finish"] * 6


def test_cpu_run_records_memory_as_not_applicable_without_cuda_probe(tmp_path) -> None:
    result, _, _, _ = _run(tmp_path)

    job = result["samples"][0]["models"]["deblur-existing"]
    assert job["timing"]["cuda_synchronized"] is False
    assert job["cuda_peak_allocated_bytes"] is None
    assert job["cuda_measurement_status"] == "not_applicable"


def test_gallery_groups_tools_and_displays_actual_overlay_images(tmp_path) -> None:
    _, output, _, _ = _run(tmp_path)
    html = (output / "sample-one" / "index.html").read_text(encoding="utf-8")

    assert html.index("去模糊") < html.index("去雾") < html.index("ROI 超分")
    assert html.count("<summary>显示检测叠图 PNG</summary>") == 10
    assert '<img src="control-fullframe-overlay.png"' in html
    assert '<img src="model-deblur-existing-overlay.png"' in html
    assert '<img src="control-roi-bicubic-x4-overlay.png"' in html


def test_fullframe_tools_export_same_aligned_fixed_roi_crop_without_detection(tmp_path) -> None:
    result, output, events, _ = _run(tmp_path)
    sample = result["samples"][0]
    fullframe = sample["controls"]["fullframe"]
    expected_box = [48, 96, 144, 144]

    assert fullframe["detail_crop"]["analysis_bbox_xyxy"] == expected_box
    for model_id in (
        "deblur-existing",
        "deblur-candidate",
        "dehaze-existing",
        "dehaze-candidate",
    ):
        crop = sample["models"][model_id]["artifact"]["detail_crop"]
        assert crop["analysis_bbox_xyxy"] == expected_box
        assert crop["source_roi_original_xyxy"] == [100, 200, 300, 300]
        with Image.open(output / crop["image"]) as image:
            assert image.size == (96, 48)

    with Image.open(output / fullframe["detail_crop"]["image"]) as control_crop:
        control_bytes = control_crop.tobytes()
    for model_id in ("deblur-existing", "dehaze-existing"):
        crop_path = output / sample["models"][model_id]["artifact"]["detail_crop"]["image"]
        with Image.open(crop_path) as model_crop:
            assert model_crop.size == (96, 48)
            # Fixture adapters are identity at scale one, proving the source area is aligned.
            assert model_crop.tobytes() == control_bytes

    html = (output / "sample-one" / "index.html").read_text(encoding="utf-8")
    assert "固定 ROI 对齐细节" in html
    assert '<img src="control-fullframe-fixed-roi.png"' in html
    assert '<img src="model-deblur-existing-fixed-roi.png"' in html
    assert sum(event.startswith("detect:") for event in events) == 9


def test_regenerate_derived_rebuilds_comparisons_crops_and_gallery_without_gpu(tmp_path) -> None:
    result, output, _, _ = _run(tmp_path)
    sample = result["samples"][0]
    raw_model = output / sample["models"]["deblur-existing"]["artifact"]["image"]
    raw_overlay = output / sample["models"]["deblur-existing"]["artifact"]["overlay"]
    raw_model_bytes = raw_model.read_bytes()
    raw_overlay_bytes = raw_overlay.read_bytes()
    raw_detection = sample["models"]["deblur-existing"]["artifact"]["detector"]

    for artifact in [
        *sample["controls"].values(),
        *(job["artifact"] for job in sample["models"].values()),
    ]:
        crop = artifact.pop("detail_crop", None)
        if crop is not None:
            (output / crop["image"]).unlink()
        if "baseline_comparison" in artifact:
            artifact["baseline_comparison"] = {"stale": True}
    stale_bytes = (json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    (output / "results.json").write_bytes(stale_bytes)
    (output / "sample-one" / "index.html").write_text("stale gallery", encoding="utf-8")

    refreshed = restoration.regenerate_derived(output)

    refreshed_sample = refreshed["samples"][0]
    refreshed_artifact = refreshed_sample["models"]["deblur-existing"]["artifact"]
    assert raw_model.read_bytes() == raw_model_bytes
    assert raw_overlay.read_bytes() == raw_overlay_bytes
    assert refreshed_artifact["detector"] == raw_detection
    assert refreshed_artifact["baseline_comparison"]["retained_baseline_indices"] == [0]
    assert (output / refreshed_artifact["detail_crop"]["image"]).is_file()
    assert "固定 ROI 对齐细节" in (output / "sample-one" / "index.html").read_text(
        encoding="utf-8"
    )
    assert (output / "results-before-derived-refresh.json").read_bytes() == stale_bytes


def test_detector_unload_failure_counts_in_summary_and_cli_exit(tmp_path, monkeypatch) -> None:
    events: list[str] = []
    result, output, _, _ = _run(
        tmp_path,
        detector=ProportionalDetector(events, unload_fail=True),
    )

    assert result["detector"]["unload_error"] == {
        "type": "RuntimeError",
        "message": "detector unload failed",
    }
    assert result["summary"]["unload_failures"] == 1
    assert "detector unload failed" in (output / "index.html").read_text(encoding="utf-8")

    monkeypatch.setattr(restoration, "evaluate", lambda *args, **kwargs: result)
    assert main(
        [
            "--manifest",
            "samples.json",
            "--models",
            "models.json",
            "--output",
            "unused-output",
            "--detector-checkpoint",
            "detector.pt",
        ]
    ) == 1


def test_unload_failure_is_recorded_but_does_not_erase_real_outputs(tmp_path) -> None:
    result, output, events, _ = _run(
        tmp_path,
        adapter_options={"dehaze-candidate": {"unload_fail": True}},
    )

    model = next(item for item in result["models"] if item["model_id"] == "dehaze-candidate")
    assert model["unload_error"] == {
        "type": "RuntimeError",
        "message": "cannot unload dehaze-candidate",
    }
    assert result["summary"]["unload_failures"] == 1
    assert result["samples"][0]["models"]["dehaze-candidate"]["status"] == "succeeded"
    assert (output / "sample-one" / "model-dehaze-candidate.png").is_file()
    assert events.count("unload:dehaze-candidate") == 1
    assert "cannot unload dehaze-candidate" in (output / "index.html").read_text(encoding="utf-8")


def test_adapter_is_unloaded_when_its_metadata_is_rejected(tmp_path) -> None:
    result, output, events, _ = _run(
        tmp_path,
        adapter_options={"deblur-candidate": {"metadata_scale": 2}},
    )

    model = next(item for item in result["models"] if item["model_id"] == "deblur-candidate")
    assert model["status"] == "failed"
    assert model["load_error"]["type"] == "ValueError"
    assert events.count("unload:deblur-candidate") == 1
    assert not (output / "sample-one" / "model-deblur-candidate.png").exists()


def test_detector_dimension_mismatch_is_an_honest_artifact_failure(tmp_path) -> None:
    samples, _ = _sample_manifest(tmp_path)
    models, kinds = _models(tmp_path)
    output = tmp_path / "evaluation"
    result = evaluate(
        samples,
        models,
        output,
        detector_checkpoint=tmp_path / "detector.pt",
        device="cpu",
        model_factory=lambda model_id, checkpoint, device: FakeAdapter(
            model_id, kinds[model_id], []
        ),
        detector_factory=lambda **kwargs: ProportionalDetector([], wrong_dimensions=True),
    )

    artifact = result["samples"][0]["controls"]["fullframe"]
    assert artifact["detector_status"] == "failed"
    assert artifact["detector_error"]["type"] == "ValueError"
    assert "dimensions" in artifact["detector_error"]["message"]
    assert "mapped_detections" not in artifact
    assert not (output / "sample-one" / "control-fullframe-overlay.png").exists()


@pytest.mark.parametrize(
    ("sample_change", "model_change", "message"),
    [
        ({"sample_id": "../escape"}, None, "sample_id"),
        ({"roi": [0, 0, 500, 100]}, None, "320"),
        ({"roi": [10, 10, 5, 20]}, None, "roi"),
        ({"reason": "  "}, None, "reason"),
        (None, "duplicate", "unique"),
        (None, "missing-kind-pair", "two models"),
    ],
)
def test_invalid_manifests_create_no_output(tmp_path, sample_change, model_change, message) -> None:
    samples, _ = _sample_manifest(tmp_path)
    models, _ = _models(tmp_path)
    if sample_change:
        rows = json.loads(samples.read_text(encoding="utf-8"))
        rows[0].update(sample_change)
        samples.write_text(json.dumps(rows), encoding="utf-8")
    if model_change == "duplicate":
        rows = json.loads(models.read_text(encoding="utf-8"))
        rows[1]["model_id"] = rows[0]["model_id"]
        models.write_text(json.dumps(rows), encoding="utf-8")
    elif model_change == "missing-kind-pair":
        rows = json.loads(models.read_text(encoding="utf-8"))[:-1]
        models.write_text(json.dumps(rows), encoding="utf-8")
    output = tmp_path / "evaluation"

    with pytest.raises(ValueError, match=message):
        evaluate(
            samples,
            models,
            output,
            detector_checkpoint=tmp_path / "detector.pt",
            device="cpu",
            model_factory=lambda *args: pytest.fail("invalid input must not load a model"),
            detector_factory=lambda **kwargs: pytest.fail("invalid input must not load detector"),
        )
    assert not output.exists()
    assert not (tmp_path / "escape").exists()


def test_existing_output_directory_is_rejected_before_any_work(tmp_path) -> None:
    samples, _ = _sample_manifest(tmp_path)
    models, _ = _models(tmp_path)
    output = tmp_path / "evaluation"
    output.mkdir()
    sentinel = output / "keep.txt"
    sentinel.write_text("prior evidence", encoding="utf-8")

    with pytest.raises(FileExistsError, match="never overwritten"):
        evaluate(
            samples,
            models,
            output,
            detector_checkpoint=tmp_path / "detector.pt",
            device="cpu",
            model_factory=lambda *args: pytest.fail("must not load"),
            detector_factory=lambda **kwargs: pytest.fail("must not load"),
        )
    assert sentinel.read_text(encoding="utf-8") == "prior evidence"
