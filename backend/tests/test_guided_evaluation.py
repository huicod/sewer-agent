import json
from pathlib import Path

import pytest
from pipe_inspector.agent.api_client import APIClientError
from pipe_inspector.agent.guided_review import GuidedResponse, GuidedReview
from pipe_inspector.contracts.inspection import ImageObservation
from pipe_inspector.evaluation.review import EvaluationManifest


def _observation(*detections: tuple[str, tuple[int, int, int, int]]) -> dict:
    return {
        "detection": {
            "detector_id": "cached-detector",
            "model_version": "cached-v1",
            "label_space_version": "sewer2403-5class-v1",
            "width": 100,
            "height": 80,
            "detections": [
                {
                    "label": label,
                    "native_label": label,
                    "score": 0.8,
                    "bbox_xyxy": list(box),
                }
                for label, box in detections
            ],
        },
        "quality": {
            "mean_luminance": 100,
            "contrast": 30,
            "sharpness": 80,
            "adequate": True,
            "reasons": [],
        },
    }


def _baseline(tmp_path: Path, observations: list[dict]) -> Path:
    samples = []
    records = []
    for index, observation in enumerate(observations):
        image = tmp_path / f"image-{index}.png"
        image.write_bytes(b"cached-image")
        samples.append(
            {
                "sample_id": f"sample-{index}",
                "group_id": f"group-{index}",
                "image_path": str(image),
                "width": 100,
                "height": 80,
                "ground_truth": [{"label": "PL", "bbox_xyxy": [10, 10, 30, 30], "score": 1.0}],
            }
        )
        records.append({"sample_id": f"sample-{index}", "observation": observation})
    path = tmp_path / "baseline.json"
    path.write_text(
        json.dumps(
            {
                "manifest": {
                    "dataset_name": "fixed-cache",
                    "split_provenance": "fixed evaluator-owned fixture",
                    "samples": samples,
                },
                "pins": {
                    "detector": {"model_version": "cached-v1", "conf": 0.25},
                    "quality_policy_version": "quality-v1",
                },
                "records": records,
            }
        ),
        encoding="utf-8",
    )
    return path


def _guided_review(
    candidates: list[dict] | None = None,
    additions: list[dict] | None = None,
) -> GuidedReview:
    return GuidedReview.model_validate(
        {
            "image_assessable": True,
            "full_image_audited": True,
            "candidates": candidates or [],
            "additional_anomalies": additions or [],
            "summary": "fixture review",
        }
    )


class _Reviewer:
    def __init__(self, result: GuidedReview | Exception):
        self.result = result
        self.calls = 0
        self.observations: list[ImageObservation] = []

    async def review(self, path: Path, observation: ImageObservation) -> GuidedReview:
        self.calls += 1
        self.observations.append(observation)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.mark.asyncio
async def test_cached_failure_is_raw_fallback_and_is_not_retried_on_resume(tmp_path) -> None:
    from pipe_inspector.evaluation.guided_run import run_cached_evaluation

    cached = _observation(("FS", (10, 10, 30, 30)))
    baseline = _baseline(tmp_path, [cached])
    output = tmp_path / "guided"
    reviewer = _Reviewer(APIClientError("timeout", retryable=True))

    first = await run_cached_evaluation(
        baseline,
        output,
        reviewer=reviewer,
        provider_settings={"base_url": "https://example.invalid/v1", "model": "fixture"},
    )
    second = await run_cached_evaluation(
        baseline,
        output,
        reviewer=reviewer,
        provider_settings={"base_url": "https://example.invalid/v1", "model": "fixture"},
    )

    assert reviewer.calls == 1
    assert reviewer.observations[0].model_dump(mode="json") == cached
    assert first["records"] == second["records"]
    record = first["records"][0]
    assert set(record) == {
        "sample_id",
        "observation",
        "review",
        "review_error",
        "predictions",
        "changes",
        "duration_seconds",
    }
    assert record["review"] is None
    assert record["review_error"] == "timeout"
    assert record["predictions"] == [
        {"label": "FS", "bbox_xyxy": [10.0, 10.0, 30.0, 30.0], "score": 0.8}
    ]
    assert record["changes"][0]["pending"] is True
    saved = (output / "state.json").read_text(encoding="utf-8")
    assert "OpenAI-compatible" not in saved
    assert "at_least_once" in saved


@pytest.mark.asyncio
async def test_resume_rejects_changed_baseline_pins_or_observation_before_call(tmp_path) -> None:
    from pipe_inspector.evaluation.guided_run import run_cached_evaluation

    baseline = _baseline(tmp_path, [_observation()])
    output = tmp_path / "guided"
    reviewer = _Reviewer(_guided_review())
    settings = {"base_url": "https://example.invalid/v1", "model": "fixture"}
    await run_cached_evaluation(baseline, output, reviewer=reviewer, provider_settings=settings)

    changed = json.loads(baseline.read_text(encoding="utf-8"))
    changed["pins"]["detector"]["conf"] = 0.3
    baseline.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="source pins"):
        await run_cached_evaluation(baseline, output, reviewer=reviewer, provider_settings=settings)

    changed["pins"]["detector"]["conf"] = 0.25
    changed["records"][0]["observation"]["quality"]["sharpness"] = 81
    baseline.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="cached observations"):
        await run_cached_evaluation(baseline, output, reviewer=reviewer, provider_settings=settings)
    assert reviewer.calls == 1


def _record(
    sample_id: str,
    observation: dict,
    predictions: list[dict],
    *,
    error: str | None = None,
    changes: list[dict] | None = None,
) -> dict:
    return {
        "sample_id": sample_id,
        "observation": observation,
        "review": None,
        "review_error": error,
        "predictions": predictions,
        "changes": changes or [],
        "duration_seconds": 2.0,
    }


def test_summary_scores_per_image_and_separates_zero_detection_recovery(tmp_path) -> None:
    from pipe_inspector.evaluation.guided_run import summarize_guided

    observations = [
        _observation(),
        _observation(("PL", (10, 10, 30, 30))),
        _observation(("BX", (10, 10, 30, 30))),
        _observation(("AJ", (10, 10, 30, 30))),
    ]
    baseline = json.loads(_baseline(tmp_path, observations).read_text(encoding="utf-8"))
    labels = ("PL", "FS", "BX", "AJ")
    for sample, label in zip(baseline["manifest"]["samples"], labels, strict=True):
        sample["ground_truth"] = [{"label": label, "bbox_xyxy": [10, 10, 30, 30], "score": 1.0}]
    manifest = EvaluationManifest.model_validate(baseline["manifest"])
    records = [
        _record(
            "sample-0",
            observations[0],
            [{"label": "PL", "bbox_xyxy": [10, 10, 30, 30], "score": 1.0}],
        ),
        _record(
            "sample-1",
            observations[1],
            [{"label": "FS", "bbox_xyxy": [10, 10, 30, 30], "score": 0.8}],
        ),
        _record("sample-2", observations[2], []),
        _record(
            "sample-3",
            observations[3],
            [{"label": "AJ", "bbox_xyxy": [10, 10, 30, 30], "score": 0.8}],
            error="timeout",
            changes=[{"pending": True}],
        ),
    ]

    summary = summarize_guided(manifest, records)

    assert summary["raw_detector"]["micro"]["tp"] == 2
    assert summary["raw_detector"]["micro"]["fp"] == 1
    assert summary["raw_detector"]["micro"]["fn"] == 2
    assert summary["fused_predictions"]["micro"] == {
        "tp": 3,
        "fp": 0,
        "fn": 1,
        "precision": 1.0,
        "recall": 0.75,
        "f1": pytest.approx(6 / 7),
    }
    assert summary["attribution"] == {
        "raw_detector_tp": 2,
        "lost_raw_detector_tp": 1,
        "raw_missed_ground_truth": 2,
        "recovered_raw_missed_ground_truth": 2,
        "zero_detection_image_count": 1,
        "zero_detection_ground_truth": 1,
        "recovered_on_zero_detection_images": 1,
    }
    assert summary["comparison"]["precision_and_recall_strictly_higher"] is True
    assert summary["review_error_count"] == 1
    assert summary["pending_change_count"] == 1
    assert summary["review_attempt_count"] == 4
    assert summary["duration_seconds"] == 8.0
    assert len(summary["per_image"]) == 4


def test_summary_never_matches_a_prediction_to_another_image_target(tmp_path) -> None:
    from pipe_inspector.evaluation.guided_run import summarize_guided

    observations = [_observation(), _observation(("PL", (10, 10, 30, 30)))]
    baseline = json.loads(_baseline(tmp_path, observations).read_text(encoding="utf-8"))
    baseline["manifest"]["samples"][1]["ground_truth"] = []
    manifest = EvaluationManifest.model_validate(baseline["manifest"])
    records = [
        _record("sample-0", observations[0], []),
        _record(
            "sample-1",
            observations[1],
            [{"label": "PL", "bbox_xyxy": [10, 10, 30, 30], "score": 0.8}],
        ),
    ]

    result = summarize_guided(manifest, records)

    assert result["fused_predictions"]["micro"]["tp"] == 0
    assert result["fused_predictions"]["micro"]["fp"] == 1
    assert result["fused_predictions"]["micro"]["fn"] == 1


class _Client:
    def __init__(self, result: GuidedResponse):
        self.result = result

    async def generate(self, messages, response_model):
        return self.result


@pytest.mark.asyncio
async def test_capturing_client_externalizes_exact_views_and_saves_pretransform_response(
    tmp_path,
) -> None:
    from pipe_inspector.evaluation.guided_run import EvidenceCapturingClient

    png = b"\x89PNG\r\nfixture"
    jpeg = b"\xff\xd8\xfffixture"
    raw = GuidedResponse.model_validate(
        {
            "coordinate_space": "normalized_0_1000",
            "image_assessable": True,
            "full_image_audited": True,
            "candidates": [],
            "additional_anomalies": [
                {
                    "label": "PL",
                    "bbox_xyxy": [100, 200, 300, 400],
                    "reason": "fixture",
                    "evidence_strength": "clear",
                }
            ],
            "summary": "fixture",
        }
    )
    client = EvidenceCapturingClient(
        _Client(raw),
        tmp_path / "requests",
        provider_settings={
            "base_url": "https://example.invalid/v1",
            "model": "fixture-model",
            "api_enable_thinking": False,
            "api_max_output_tokens": 512,
        },
    )
    client.start_sample("sample/hostile")
    messages = [
        {"role": "system", "content": "fixture prompt"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": '{"fixture":true}'},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/png;base64,"
                        + __import__("base64").b64encode(png).decode()
                    },
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/jpeg;base64,"
                        + __import__("base64").b64encode(jpeg).decode()
                    },
                },
            ],
        },
    ]

    assert await client.generate(messages, GuidedResponse) == raw

    request_dir = tmp_path / "requests" / "0001"
    assert (request_dir / "view-0.png").read_bytes() == png
    assert (request_dir / "view-1.jpg").read_bytes() == jpeg
    request = json.loads((request_dir / "request.json").read_text(encoding="utf-8"))
    rendered = json.dumps(request)
    assert "base64" not in rendered
    assert "Authorization" not in rendered
    assert request["sample_id"] == "sample/hostile"
    assert request["payload"]["model"] == "fixture-model"
    assert request["payload"]["messages"][1]["content"][1]["image_file"] == "view-0.png"
    response = json.loads((request_dir / "response.json").read_text(encoding="utf-8"))
    assert response["additional_anomalies"][0]["bbox_xyxy"] == [100.0, 200.0, 300.0, 400.0]


@pytest.mark.asyncio
async def test_runner_rejects_more_than_48_cached_images_before_review(tmp_path) -> None:
    from pipe_inspector.evaluation.guided_run import run_cached_evaluation

    reviewer = _Reviewer(_guided_review())
    baseline = _baseline(tmp_path, [_observation()] * 49)
    with pytest.raises(ValueError, match="max_samples"):
        await run_cached_evaluation(
            baseline,
            tmp_path / "guided",
            reviewer=reviewer,
            provider_settings={"base_url": "https://example.invalid/v1", "model": "fixture"},
        )
    assert reviewer.calls == 0


def test_provider_evidence_counts_crash_gap_separately_from_completed_samples(tmp_path):
    from pipe_inspector.evaluation.guided_run import request_accounting

    assert request_accounting(tmp_path)["request_records"] == 0
    for attempt in ("0001", "0002"):
        directory = tmp_path / "requests" / attempt
        directory.mkdir(parents=True)
        (directory / "request.json").write_text('{"sample_id":"same-image"}')
    (tmp_path / "requests" / "0002" / "response.json").write_text("{}")
    assert request_accounting(tmp_path) == {
        "request_records": 2,
        "validated_responses": 1,
        "client_errors": 0,
        "unresolved_requests": 1,
        "billable_calls": None,
    }
