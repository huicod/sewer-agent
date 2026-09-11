import json
from copy import deepcopy

import pytest
from PIL import Image
from pipe_inspector.contracts.open_scan import OpenDetectionBatch


def sample(tmp_path, *, empty=False):
    path = tmp_path / "source.png"
    Image.new("RGB", (100, 80)).save(path)
    boxes = (
        []
        if empty
        else [dict(label="BX", native_label="BX", score=0.95, bbox_xyxy=[10, 10, 50, 50])]
    )
    return dict(
        sample_id="fixture",
        group_id="source:fixture",
        source_kind="image",
        image_path=str(path),
        width=100,
        height=80,
        ground_truth=[],
        observation=dict(
            detection=dict(
                detector_id="primary",
                model_version="v1",
                label_space_version="sewer2403-5class-v1",
                width=100,
                height=80,
                detections=boxes,
            ),
            quality=dict(mean_luminance=100, contrast=50, sharpness=50, adequate=True, reasons=[]),
        ),
        review=dict(
            image_assessable=True,
            full_image_audited=True,
            candidates=[],
            additional_anomalies=[
                dict(
                    label="OTHER",
                    name="roots",
                    reason="visible",
                    bbox_xyxy=[50, 50, 70, 70],
                    evidence_strength="uncertain",
                )
            ],
            summary="review",
        ),
        review_error=None,
        baseline_policy="guided-confidence-v2",
        baseline_predictions=[
            {k: v for k, v in box.items() if k != "native_label"} for box in boxes
        ],
        changes=[],
        source_pins={"model": "cached"},
    )


class Detector:
    detector_id = "open"
    model_version = "open-v1"
    vocabulary_version = "v1"

    def __init__(self, *, fail=False):
        self.calls = 0
        self.unloads = 0
        self.fail = fail

    def predict_image(self, path):
        self.calls += 1
        if self.fail:
            raise RuntimeError("do not leak implementation or secret")
        return OpenDetectionBatch.model_validate(
            dict(
                detector_id=self.detector_id,
                model_version=self.model_version,
                vocabulary_version=self.vocabulary_version,
                width=100,
                height=80,
                candidates=[
                    dict(label="BX", name="duplicate", score=0.5, bbox_xyxy=[10, 10, 50, 50]),
                    dict(label="BLOCK", name="blockage", score=0.5, bbox_xyxy=[10, 10, 50, 50]),
                    dict(label="OTHER", name="roots", score=0.5, bbox_xyxy=[60, 60, 90, 79]),
                ],
            )
        )

    def unload(self):
        self.unloads += 1


def run(tmp_path, samples, detector, **kwargs):
    from pipe_inspector.evaluation.incremental import run_incremental

    return run_incremental(
        samples, tmp_path / "out", detector=detector, configuration={"confidence": 0.25}, **kwargs
    )


def test_additive_fusion_uses_production_dedup_and_preserves_high_confidence(tmp_path):
    item = sample(tmp_path)
    detector = Detector()
    result = run(tmp_path, [item], detector)
    record = result["records"][0]
    assert record["trigger"] == "suspected_miss"
    assert record["guided"] == item["baseline_predictions"]
    assert record["combined"][0] == item["baseline_predictions"][0]
    assert [x["label"] for x in record["open_added"]] == ["BLOCK", "OTHER"]
    assert [x["label"] for x in record["combined"]] == ["BX", "BLOCK"]
    assert all(x["review_status"] == "needs_review" for x in record["open_findings"])
    assert detector.calls == detector.unloads == 1


def test_resume_reuses_success_and_rejects_changed_inputs_and_config(tmp_path):
    item, detector = sample(tmp_path), Detector()
    first = run(tmp_path, [item], detector)
    assert run(tmp_path, [item], detector)["records"] == first["records"]
    assert detector.calls == 1
    changed = deepcopy(item)
    changed["source_pins"]["model"] = "different"
    with pytest.raises(ValueError, match="snapshot"):
        run(tmp_path, [changed], detector)
    from pipe_inspector.evaluation.incremental import run_incremental

    with pytest.raises(ValueError, match="snapshot"):
        run_incremental(
            [item], tmp_path / "out", detector=detector, configuration={"confidence": 0.3}
        )
    assert detector.calls == 1


def test_failure_is_checkpointed_not_retried_and_not_reported_as_success(tmp_path):
    item, detector = sample(tmp_path), Detector(fail=True)
    first = run(tmp_path, [item], detector)
    second = run(tmp_path, [item], detector)
    record = second["records"][0]
    assert first["records"] == second["records"]
    assert record["open_status"] == "failed"
    assert record["open_error"] == "open_scan_failed"
    assert record["combined"] == record["guided"]
    assert detector.calls == detector.unloads == 1
    assert "do not leak" not in (tmp_path / "out/state.json").read_text()


def test_offline_session_can_own_model_lifetime_without_changing_default(tmp_path):
    detector = Detector()
    result = run(tmp_path, [sample(tmp_path)], detector, unload_after_sample=False)
    assert result["records"][0]["open_status"] == "succeeded"
    assert detector.calls == 1
    assert detector.unloads == 0


@pytest.mark.parametrize(
    ("empty", "adequate", "expected"),
    [
        (False, True, None),
        (True, True, "negative_audit"),
        (True, False, None),
    ],
)
def test_conditional_trigger_does_not_scan_every_image(tmp_path, empty, adequate, expected):
    item, detector = sample(tmp_path, empty=empty), Detector()
    item["review"]["additional_anomalies"] = []
    item["observation"]["quality"]["adequate"] = adequate
    result = run(tmp_path, [item], detector)["records"][0]
    assert result["trigger"] == expected
    assert detector.calls == (1 if expected else 0)


def test_missing_image_and_unknown_video_annotations_are_not_normal(tmp_path):
    item = sample(tmp_path)
    item.update(
        source_kind="video",
        ground_truth=None,
        frame_index=7,
        timestamp_seconds=0.28,
        baseline_policy="saved-video-review-v1",
    )
    item["review"]["additional_anomalies"][0].pop("evidence_strength")
    item["image_path"] = str(tmp_path / "missing.png")
    detector = Detector()
    record = run(tmp_path, [item], detector)["records"][0]
    assert record["ground_truth"] is None
    assert record["open_status"] == "failed"
    assert record["open_error"] == "source_unavailable"
    assert detector.calls == 0


def test_duplicate_and_invalid_bounds_rejected_before_model_call(tmp_path):
    item, detector = sample(tmp_path), Detector()
    with pytest.raises(ValueError, match="duplicate"):
        run(tmp_path, [item, item], detector)
    item["baseline_predictions"][0]["bbox_xyxy"][2] = 200
    with pytest.raises(ValueError, match="bounds"):
        run(tmp_path, [item], detector)
    assert detector.calls == 0


def test_invalid_cached_record_cannot_be_silently_reused(tmp_path):
    item, detector = sample(tmp_path), Detector()
    run(tmp_path, [item], detector)
    path = tmp_path / "out/state.json"
    saved = json.loads(path.read_text())
    saved["records"][0]["combined"] = []
    path.write_text(json.dumps(saved))
    with pytest.raises(ValueError, match="record"):
        run(tmp_path, [item], detector)
    assert detector.calls == 1


def test_cached_batch_version_is_validated_even_when_candidate_boxes_unchanged(tmp_path):
    item, detector = sample(tmp_path), Detector()
    run(tmp_path, [item], detector)
    path = tmp_path / "out/state.json"
    saved = json.loads(path.read_text())
    saved["records"][0]["open_batch"]["vocabulary_version"] = "different"
    # Recompute fusion to model a self-consistent result from a different model version.
    from pipe_inspector.evaluation.incremental import _record

    changed = saved["records"][0]
    saved["records"][0] = _record(
        saved["samples"][0],
        OpenDetectionBatch.model_validate(changed["open_batch"]),
        changed["open_status"],
        changed["open_error"],
        changed["open_duration_seconds"],
    )
    path.write_text(json.dumps(saved))
    with pytest.raises(ValueError, match="pins"):
        run(tmp_path, [item], detector)
    assert detector.calls == 1


def test_resume_only_executes_unfinished_sample_after_interruption(tmp_path):
    first, second = sample(tmp_path), sample(tmp_path)
    second["sample_id"], second["group_id"] = "second", "source:second"

    class Interrupted(Detector):
        def predict_image(self, path):
            if self.calls == 1:
                raise KeyboardInterrupt()
            return super().predict_image(path)

    interrupted = Interrupted()
    with pytest.raises(KeyboardInterrupt):
        run(tmp_path, [first, second], interrupted)
    saved = json.loads((tmp_path / "out/state.json").read_text())
    assert len(saved["records"]) == 1
    resumed = Detector()
    assert len(run(tmp_path, [first, second], resumed)["records"]) == 2
    assert resumed.calls == 1


def test_guided_loader_requires_exact_records_and_accepted_cached_predictions(tmp_path):
    from pipe_inspector.agent.guided_review import GuidedReview
    from pipe_inspector.contracts.inspection import ImageObservation
    from pipe_inspector.evaluation.guided import fuse_guided
    from pipe_inspector.evaluation.incremental import load_guided_samples

    item = sample(tmp_path)
    fused = fuse_guided(
        ImageObservation.model_validate(item["observation"]),
        GuidedReview.model_validate(item["review"]),
    )
    state = dict(
        schema_version="guided-cached-evaluation-v1",
        manifest=dict(
            dataset_name="fixture",
            split_provenance="test, not an independent split",
            samples=[
                {
                    key: item[key]
                    for key in (
                        "sample_id",
                        "group_id",
                        "image_path",
                        "width",
                        "height",
                        "ground_truth",
                    )
                }
            ],
        ),
        records=[
            dict(
                sample_id=item["sample_id"],
                observation=item["observation"],
                review=item["review"],
                review_error=None,
                **fused,
            )
        ],
        run_pins={"fusion": {"version": "guided-confidence-v2"}},
        source_pins={},
    )
    path = tmp_path / "guided.json"
    path.write_text(json.dumps(state))
    assert load_guided_samples(path)[0]["baseline_predictions"] == fused["predictions"]
    state["run_pins"]["fusion"]["version"] = "old-policy"
    path.write_text(json.dumps(state))
    with pytest.raises(ValueError, match="fusion pin"):
        load_guided_samples(path)
    state["run_pins"]["fusion"]["version"] = "guided-confidence-v2"
    state["records"][0]["predictions"] = []
    path.write_text(json.dumps(state))
    with pytest.raises(ValueError, match="fusion policy"):
        load_guided_samples(path)
    state["records"] = []
    path.write_text(json.dumps(state))
    with pytest.raises(ValueError, match="exactly cover"):
        load_guided_samples(path)


def test_fixed_cohort_rejects_omitted_or_substituted_samples():
    from pipe_inspector.evaluation.incremental import validate_fixed_cohort

    entries = [
        dict(
            sample_id=str(i),
            group_id=str(i),
            source_kind="image" if i < 48 else "video",
            baseline_policy="guided-confidence-v2" if i < 48 else "saved-video-review-v1",
        )
        for i in range(56)
    ]
    manifest = dict(benchmark_id="t11-sewer2403-video-v1", samples=entries)
    assert validate_fixed_cohort(entries, manifest) == 56
    with pytest.raises(ValueError, match="fixed cohort"):
        validate_fixed_cohort(entries[:48], manifest)
    changed = deepcopy(entries)
    changed[0]["sample_id"] = "replacement"
    with pytest.raises(ValueError, match="fixed cohort"):
        validate_fixed_cohort(changed, manifest)


def test_detector_parameters_cannot_disagree_with_saved_configuration(tmp_path):
    detector = Detector()
    detector.confidence = 0.7
    with pytest.raises(ValueError, match="configuration"):
        run(tmp_path, [sample(tmp_path)], detector)
    assert detector.calls == 0


def test_video_loader_requires_recovered_cache_schema(tmp_path):
    from pipe_inspector.evaluation.incremental import load_video_samples

    path = tmp_path / "video.json"
    path.write_text(json.dumps(dict(samples=[], provenance={})))
    with pytest.raises(ValueError, match="video cache"):
        load_video_samples(path)
