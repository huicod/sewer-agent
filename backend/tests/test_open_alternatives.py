import json
from copy import deepcopy

import pytest
from test_incremental_evaluation import Detector, sample


def test_run_model_releases_once_and_exports_existing_stage_evidence(tmp_path):
    from pipe_inspector.evaluation.open_alternatives import run_model

    detector = Detector()
    item = sample(tmp_path)
    second = deepcopy(item) | {"sample_id": "second", "group_id": "second"}
    state = run_model([item, second], tmp_path / "out", detector=detector, configuration={})
    assert detector.calls == 2 and detector.unloads == 1
    assert all(r["guided"] == item["baseline_predictions"] for r in state["records"])
    assert (tmp_path / "out/gallery/sample-0001/open-candidates.svg").is_file()
    assert (tmp_path / "out/gallery/sample-0001/baseline-guided.svg").is_file()
    assert (tmp_path / "out/review-queue.csv").is_file()
    assert json.loads((tmp_path / "out/summary.json").read_text())["complete"]
    run_model([item, second], tmp_path / "out", detector=detector, configuration={})
    assert detector.calls == 2  # restart uses results, not inference


def test_run_model_releases_even_if_snapshot_validation_raises(tmp_path):
    from pipe_inspector.evaluation.open_alternatives import run_model

    detector = Detector()
    with pytest.raises(ValueError):
        run_model([], tmp_path / "out", detector=detector, configuration={})
    assert detector.unloads == 1


def test_comparison_requires_identical_baseline_and_retains_other(tmp_path):
    from pipe_inspector.evaluation.open_alternatives import export_comparison, run_model

    state = run_model([sample(tmp_path)], tmp_path / "model", detector=Detector(), configuration={})
    comparison = export_comparison({"a": state, "b": deepcopy(state)}, tmp_path)
    assert comparison["models"]["a"]["pending_other"] == 1
    assert comparison["models"]["a"]["added_annotation_matches"] == 0
    html = (tmp_path / "index.html").read_text()
    assert "a/gallery/sample-0001/open-candidates.svg" in html
    assert "b/gallery/sample-0001/index.html" in html
    assert "待人工确认" in html
    changed = deepcopy(state)
    changed["records"][0]["guided"] = []
    with pytest.raises(ValueError, match="baseline"):
        export_comparison({"a": state, "b": changed}, tmp_path)


def test_comparison_rejects_missing_or_reordered_samples(tmp_path):
    from pipe_inspector.evaluation.open_alternatives import export_comparison, run_model

    state = run_model([sample(tmp_path)], tmp_path / "model", detector=Detector(), configuration={})
    changed = deepcopy(state)
    changed["records"] = []
    with pytest.raises(ValueError, match="coverage"):
        export_comparison({"a": state, "b": changed}, tmp_path)


@pytest.mark.parametrize("tamper", [None, "samples", "records", "identity"])
def test_reference_pins_exact_48_images_and_frozen_results(tmp_path, monkeypatch, tamper):
    from pipe_inspector.evaluation import open_alternatives as module
    from pipe_inspector.evaluation.incremental import _record, validate_samples

    item = sample(tmp_path)
    samples = validate_samples(
        [deepcopy(item) | {"sample_id": f"image-{i}", "group_id": f"source-{i}"} for i in range(48)]
    )
    videos = [
        {
            "sample_id": f"video-{i}",
            "group_id": f"video-{i}",
            "source_kind": "video",
            "baseline_policy": "saved-video-review-v1",
        }
        for i in range(8)
    ]
    reference = dict(
        samples=deepcopy(samples) + videos,
        records=[_record(s, None, "skipped", None, 0.0) for s in samples],
        pins={"detector": {"detector_id": "yoloe"}},
    )
    manifest = dict(
        benchmark_id="t11-sewer2403-video-v1",
        samples=[
            {k: s[k] for k in ("sample_id", "group_id", "source_kind", "baseline_policy")}
            for s in reference["samples"]
        ],
    )
    if tamper == "samples":
        reference["samples"][0]["baseline_predictions"] = []
    elif tamper == "records":
        reference["records"][0]["guided"] = []
    elif tamper == "identity":
        reference["samples"][0]["group_id"] = "changed"
    monkeypatch.setattr(module, "load_guided_samples", lambda path: samples)
    reference_path, manifest_path = tmp_path / "reference.json", tmp_path / "manifest.json"
    reference_path.write_text(json.dumps(reference))
    manifest_path.write_text(json.dumps(manifest))
    if tamper:
        with pytest.raises(ValueError):
            module.load_reference(reference_path, manifest_path, [tmp_path / "cached"])
    else:
        result = module.load_reference(reference_path, manifest_path, [tmp_path / "cached"])
        assert result["samples"] == samples
        assert len(result["records"]) == 48
