import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pipe_inspector.contracts.detection import Detection, DetectionBatch
from pipe_inspector.contracts.inspection import (
    CandidateReview,
    ImageReview,
    QualitySummary,
)
from pipe_inspector.evaluation.review import (
    EvaluationManifest,
    LabeledBox,
    build_live_components,
    cli_main,
    evaluate_manifest,
    score_boxes,
    summarize,
)
from pydantic import SecretStr, ValidationError


def box(label, xyxy, score=1.0):
    return LabeledBox(label=label, bbox_xyxy=xyxy, score=score)


def manifest(tmp_path: Path, count: int = 1) -> EvaluationManifest:
    samples = []
    for index in range(count):
        image = tmp_path / f"image-{index}.jpg"
        image.write_bytes(b"fixture")
        samples.append(
            {
                "sample_id": f"sample-{index}",
                "group_id": f"group-{index}",
                "image_path": str(image),
                "width": 100,
                "height": 80,
                "ground_truth": [{"label": "PL", "bbox_xyxy": [10, 10, 30, 30]}],
            }
        )
    return EvaluationManifest(
        dataset_name="held-out-fixture",
        split_provenance="Fixed test split supplied by evaluator owner.",
        samples=samples,
    )


def detection_batch(*detections: Detection) -> DetectionBatch:
    return DetectionBatch(
        detector_id="fixture-detector",
        model_version="fixture-v1",
        label_space_version="sewer2403-5class-v1",
        width=100,
        height=80,
        detections=detections,
    )


class FakeDetector:
    def __init__(self, result: DetectionBatch):
        self.result = result
        self.calls = 0

    def predict_image(self, path):
        self.calls += 1
        return self.result


class FakeReviewer:
    def __init__(self, result: ImageReview | Exception):
        self.result = result
        self.calls = 0

    async def review(self, path, observation):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FailingSecondDetector:
    def __init__(self):
        self.calls = []

    def predict_image(self, path):
        self.calls.append(Path(path).name)
        if Path(path).name == "image-1.jpg":
            raise RuntimeError("local detector failure")
        return detection_batch(
            Detection(label="PL", native_label="PL", score=0.9, bbox_xyxy=(10, 10, 30, 30))
        )


def adequate_quality(path):
    return QualitySummary(
        mean_luminance=100,
        contrast=30,
        sharpness=80,
        adequate=True,
        reasons=(),
    )


def test_matching_is_class_aware_greedy_and_one_to_one() -> None:
    ground_truth = [
        box("PL", (0, 0, 10, 10)),
        box("BX", (20, 20, 30, 30)),
    ]
    predictions = [
        box("PL", (0, 0, 10, 10), 0.9),
        box("PL", (0, 0, 10, 10), 0.8),
        box("FS", (20, 20, 30, 30), 0.7),
    ]

    result = score_boxes(ground_truth, predictions)

    assert result["micro"] == {
        "tp": 1,
        "fp": 2,
        "fn": 1,
        "precision": pytest.approx(1 / 3),
        "recall": pytest.approx(1 / 2),
        "f1": pytest.approx(0.4),
    }
    assert result["per_class"]["PL"]["tp"] == 1
    assert result["per_class"]["PL"]["fp"] == 1
    assert result["per_class"]["BX"]["fn"] == 1
    assert result["per_class"]["FS"]["fp"] == 1


@pytest.mark.parametrize(
    "change",
    [
        {"dataset_name": ""},
        {"split_provenance": ""},
        {"split_provenance": "   "},
        {"samples": []},
        {
            "samples": [
                {
                    "sample_id": "same",
                    "group_id": "group-a",
                    "image_path": "/tmp/a.jpg",
                    "width": 20,
                    "height": 20,
                    "ground_truth": [],
                },
                {
                    "sample_id": "same",
                    "group_id": "group-b",
                    "image_path": "/tmp/b.jpg",
                    "width": 20,
                    "height": 20,
                    "ground_truth": [],
                },
            ]
        },
        {
            "samples": [
                {
                    "sample_id": "one",
                    "group_id": "same",
                    "image_path": "/tmp/a.jpg",
                    "width": 20,
                    "height": 20,
                    "ground_truth": [],
                },
                {
                    "sample_id": "two",
                    "group_id": "same",
                    "image_path": "/tmp/b.jpg",
                    "width": 20,
                    "height": 20,
                    "ground_truth": [],
                },
            ]
        },
    ],
)
def test_manifest_rejects_empty_or_nonunique_identity(change) -> None:
    base = {
        "dataset_name": "fixed",
        "split_provenance": "operator supplied",
        "samples": [
            {
                "sample_id": "one",
                "group_id": "group-one",
                "image_path": "/tmp/a.jpg",
                "width": 20,
                "height": 20,
                "ground_truth": [],
            }
        ],
    }
    with pytest.raises(ValidationError):
        EvaluationManifest(**(base | change))


def test_manifest_rejects_out_of_bounds_ground_truth_and_sample_limit(tmp_path) -> None:
    values = manifest(tmp_path).model_dump(mode="json")
    values["samples"][0]["ground_truth"][0]["bbox_xyxy"] = [10, 10, 101, 30]
    with pytest.raises(ValidationError, match="ground-truth box"):
        EvaluationManifest(**values)

    with pytest.raises(ValueError, match="max_samples"):
        manifest(tmp_path, count=2).enforce_limit(1)

    values = manifest(tmp_path).model_dump(mode="json")
    values["samples"][0]["image_path"] = "relative/image.jpg"
    with pytest.raises(ValidationError, match="absolute local path"):
        EvaluationManifest(**values)


@pytest.mark.asyncio
async def test_evaluation_keeps_pending_queue_separate_from_confirmed(tmp_path) -> None:
    detector = FakeDetector(
        detection_batch(
            Detection(label="FS", native_label="FS", score=0.8, bbox_xyxy=(10, 10, 30, 30))
        )
    )
    reviewer = FakeReviewer(
        ImageReview(
            image_assessable=True,
            full_image_audited=True,
            candidates=(
                CandidateReview(
                    index=0,
                    verdict="revise",
                    proposed_label="PL",
                    reason="revise fixture",
                ),
            ),
            additional_anomalies=(),
            summary="fixture review",
        )
    )

    records = await evaluate_manifest(
        manifest(tmp_path),
        tmp_path / "run",
        pins={"detector": "fixture-v1", "vlm": "fixture-review-v1"},
        detector=detector,
        reviewer=reviewer,
        quality_analyzer=adequate_quality,
    )
    result = summarize(manifest(tmp_path), records)

    assert result["complete"] is True
    assert result["missing_sample_count"] == 0
    assert result["raw_detector"]["micro"]["tp"] == 0
    assert result["retained_queue"]["micro"]["tp"] == 1
    assert result["auto_confirmed"]["micro"]["fn"] == 1
    assert result["pending_findings"] == 1
    assert result["paired_image_label_sets"] == {
        "improved": 1,
        "worsened": 0,
        "unchanged": 0,
    }
    assert result["paired_deltas"]["retained_queue_minus_raw"]["micro_f1"] == 1.0
    assert result["claims"]["true_normal_accuracy"] is None
    assert result["operating_point"] == {"conf": 0.25, "imgsz": 960, "iou": 0.5}


@pytest.mark.asyncio
async def test_saved_failure_stays_in_denominator_without_second_api_call(tmp_path) -> None:
    detector = FakeDetector(
        detection_batch(
            Detection(label="FS", native_label="FS", score=0.7, bbox_xyxy=(10, 10, 30, 30))
        )
    )
    reviewer = FakeReviewer(RuntimeError("provider detail must not persist"))
    run_dir = tmp_path / "run"
    fixed_manifest = manifest(tmp_path)

    first = await evaluate_manifest(
        fixed_manifest,
        run_dir,
        pins={"detector": "fixture-v1", "vlm": "fixture-review-v1"},
        detector=detector,
        reviewer=reviewer,
        quality_analyzer=adequate_quality,
    )
    second = await evaluate_manifest(
        fixed_manifest,
        run_dir,
        pins={"detector": "fixture-v1", "vlm": "fixture-review-v1"},
        detector=detector,
        reviewer=reviewer,
        quality_analyzer=adequate_quality,
    )
    result = summarize(fixed_manifest, second)

    assert detector.calls == 1
    assert reviewer.calls == 1
    assert first == second
    assert result["review_failures"] == 1
    assert result["raw_detector"]["micro"]["tp"] == 0
    assert result["raw_detector"]["micro"]["fn"] == 1
    assert result["retained_queue"]["micro"]["fp"] == 1
    assert result["retained_queue"]["micro"]["fn"] == 1
    assert result["pending_findings"] == 1
    saved = (run_dir / "state.json").read_text(encoding="utf-8")
    assert "provider detail" not in saved


@pytest.mark.asyncio
async def test_resume_rejects_manifest_or_pin_mismatch_before_calls(tmp_path) -> None:
    detector = FakeDetector(detection_batch())
    reviewer = FakeReviewer(
        ImageReview(
            image_assessable=True,
            full_image_audited=True,
            candidates=(),
            additional_anomalies=(),
            summary="fixture review",
        )
    )
    fixed_manifest = manifest(tmp_path)
    run_dir = tmp_path / "run"
    pins = {
        "detector": {"model_version": "fixture-v1"},
        "vlm": {
            "model": "fixture-review-v1",
            "api_timeout_seconds": 17,
            "api_enable_thinking": False,
            "api_max_output_tokens": 321,
            "vlm_max_image_bytes": 4567,
        },
        "quality_policy_version": "image-quality-v1",
        "image_decision_policy": {"strong_evidence_score": 0.85},
    }
    await evaluate_manifest(
        fixed_manifest,
        run_dir,
        pins=pins,
        detector=detector,
        reviewer=reviewer,
        quality_analyzer=adequate_quality,
    )

    with pytest.raises(ValueError, match="pins do not match"):
        changed_pins = json.loads(json.dumps(pins))
        changed_pins["image_decision_policy"]["strong_evidence_score"] = 0.9
        await evaluate_manifest(
            fixed_manifest,
            run_dir,
            pins=changed_pins,
            detector=detector,
            reviewer=reviewer,
            quality_analyzer=adequate_quality,
        )

    changed = fixed_manifest.model_copy(update={"split_provenance": "different source"})
    with pytest.raises(ValueError, match="manifest does not match"):
        await evaluate_manifest(
            changed,
            run_dir,
            pins=pins,
            detector=detector,
            reviewer=reviewer,
            quality_analyzer=adequate_quality,
        )
    assert detector.calls == 1
    assert reviewer.calls == 1


@pytest.mark.asyncio
async def test_summary_never_matches_boxes_across_different_images(tmp_path) -> None:
    fixed_manifest = manifest(tmp_path, count=2)
    values = fixed_manifest.model_dump(mode="json")
    values["samples"][1]["ground_truth"] = []
    fixed_manifest = EvaluationManifest(**values)
    detector = FakeDetector(
        detection_batch(
            Detection(label="PL", native_label="PL", score=0.9, bbox_xyxy=(10, 10, 30, 30))
        )
    )
    reviewer = FakeReviewer(
        ImageReview(
            image_assessable=True,
            full_image_audited=True,
            candidates=(CandidateReview(index=0, verdict="accept", reason="fixture"),),
            additional_anomalies=(),
            summary="fixture review",
        )
    )

    records = await evaluate_manifest(
        fixed_manifest,
        tmp_path / "run-cross-image",
        pins={"detector": "fixture-v1", "vlm": "fixture-review-v1"},
        detector=detector,
        reviewer=reviewer,
        quality_analyzer=adequate_quality,
    )
    records[0]["observation"]["detection"]["detections"] = []
    records[0]["decision"]["findings"] = []

    result = summarize(fixed_manifest, records)

    assert result["raw_detector"]["micro"]["tp"] == 0
    assert result["raw_detector"]["micro"]["fp"] == 1
    assert result["raw_detector"]["micro"]["fn"] == 1


@pytest.mark.asyncio
async def test_high_confidence_pl_does_not_spend_review_call(tmp_path) -> None:
    detector = FakeDetector(
        detection_batch(
            Detection(label="PL", native_label="PL", score=0.9, bbox_xyxy=(10, 10, 30, 30))
        )
    )
    reviewer = FakeReviewer(RuntimeError("must not be called"))

    records = await evaluate_manifest(
        manifest(tmp_path),
        tmp_path / "run-skip",
        pins={"detector": "fixture-v1", "vlm": "fixture-review-v1"},
        detector=detector,
        reviewer=reviewer,
        quality_analyzer=adequate_quality,
    )

    assert reviewer.calls == 0
    assert records[0]["review"] is None
    assert records[0]["review_error"] is None


def test_state_is_plain_json_without_normal_claim(tmp_path) -> None:
    payload = manifest(tmp_path).model_dump(mode="json")
    rendered = json.dumps(payload)
    assert "normal" not in rendered.lower()


def test_summary_cli_only_reads_saved_state(tmp_path, capsys) -> None:
    fixed_manifest = manifest(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(fixed_manifest.model_dump_json(), encoding="utf-8")
    output = tmp_path / "saved"
    output.mkdir()
    (output / "state.json").write_text(
        json.dumps(
            {
                "manifest": fixed_manifest.model_dump(mode="json"),
                "pins": {"detector": "fixture", "vlm": "fixture"},
                "records": [],
            }
        ),
        encoding="utf-8",
    )

    assert cli_main(["--manifest", str(manifest_path), "--output", str(output)]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "complete": False,
        "evaluated_sample_count": 0,
        "evaluated_sample_ids": [],
        "expected_sample_count": 1,
        "expected_sample_ids": ["sample-0"],
        "missing_sample_count": 1,
        "missing_sample_ids": ["sample-0"],
        "status": "incomplete",
    }
    assert list(output.iterdir()) == [output / "state.json"]


def test_missing_vlm_configuration_fails_before_detector_construction(monkeypatch) -> None:
    class MissingSettings:
        vlm_configured = False

    constructed = False

    def forbidden(*args, **kwargs):
        nonlocal constructed
        constructed = True

    monkeypatch.setattr("pipe_inspector.vision.detector.UltralyticsDetector", forbidden)
    with pytest.raises(ValueError, match="VLM configuration"):
        build_live_components(MissingSettings())
    assert constructed is False


@pytest.mark.asyncio
async def test_local_failure_leaves_incomplete_run_and_resume_skips_cached_sample(tmp_path) -> None:
    fixed_manifest = manifest(tmp_path, count=2)
    detector = FailingSecondDetector()
    reviewer = FakeReviewer(RuntimeError("review is not eligible"))
    run_dir = tmp_path / "interrupted"
    pins = {"detector": "fixture-v1", "vlm": "fixture-review-v1"}

    with pytest.raises(RuntimeError, match="local detector failure"):
        await evaluate_manifest(
            fixed_manifest,
            run_dir,
            pins=pins,
            detector=detector,
            reviewer=reviewer,
            quality_analyzer=adequate_quality,
        )
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    partial = summarize(fixed_manifest, state["records"])

    assert partial == {
        "status": "incomplete",
        "complete": False,
        "expected_sample_count": 2,
        "expected_sample_ids": ["sample-0", "sample-1"],
        "evaluated_sample_count": 1,
        "evaluated_sample_ids": ["sample-0"],
        "missing_sample_count": 1,
        "missing_sample_ids": ["sample-1"],
    }

    with pytest.raises(RuntimeError, match="local detector failure"):
        await evaluate_manifest(
            fixed_manifest,
            run_dir,
            pins=pins,
            detector=detector,
            reviewer=reviewer,
            quality_analyzer=adequate_quality,
        )
    assert detector.calls == ["image-0.jpg", "image-1.jpg", "image-1.jpg"]
    assert reviewer.calls == 0


def test_live_pins_cover_all_response_and_failure_affecting_configuration() -> None:
    settings = SimpleNamespace(
        vlm_configured=True,
        vlm_base_url="https://example.invalid/v1",
        vlm_model="fixture-vlm",
        vlm_api_key=SecretStr("fixture-only"),
        api_timeout_seconds=17,
        api_enable_thinking=False,
        api_max_output_tokens=321,
        vlm_max_image_bytes=4567,
        detector_path=Path("/tmp/fixture.pt"),
        detector_device="cpu",
    )

    _, _, pins = build_live_components(settings)

    assert pins["vlm"] == {
        "base_url": "https://example.invalid/v1",
        "model": "fixture-vlm",
        "prompt_version": "image-review-v1",
        "review_score_threshold": 0.85,
        "review_always_labels": ["BX", "FS"],
        "api_enable_thinking": False,
        "api_max_output_tokens": 321,
        "api_timeout_seconds": 17,
        "vlm_max_image_bytes": 4567,
    }
    assert pins["detector"]["detector_id"] == "ultralytics-yolo26"
    assert pins["detector"]["label_space_version"] == "sewer2403-5class-v1"
    assert pins["quality_policy_version"] == "image-quality-v1"
    assert pins["image_decision_policy"] == {"strong_evidence_score": 0.85}
