"""Exercise the real online reviewer/finalizer; only the provider is local HTTP."""

import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from PIL import Image
from pipe_inspector.agent.api_client import OpenAICompatibleClient
from pipe_inspector.config import Settings
from pipe_inspector.contracts.detection import Detection, DetectionBatch
from pipe_inspector.contracts.inspection import ImageObservation, QualitySummary
from pipe_inspector.workflow.activities import ImageActivities
from pipe_inspector.workflow.start import run_config
from temporalio.testing import ActivityEnvironment

from backend.tests.test_image_activities import Store


@pytest.mark.asyncio
@pytest.mark.parametrize("score", [None, 0.4, 0.95])
async def test_default_online_crops_and_confidence_fusion_survive_checkpoint_replay(
    tmp_path, monkeypatch, score
):
    store = Store()
    batch = DetectionBatch(
        detector_id="fixture",
        model_version="v1",
        label_space_version="five",
        width=320,
        height=240,
        detections=()
        if score is None
        else (Detection(label="BX", native_label="BX", score=score, bbox_xyxy=(20, 30, 80, 100)),),
    )
    store.steps["detect"] = ImageObservation(
        detection=batch,
        quality=QualitySummary(mean_luminance=100, contrast=30, sharpness=80, adequate=True),
    ).model_dump(mode="json")
    settings = Settings(
        _env_file=None,
        vlm_model="fixture",
        vlm_base_url="https://vlm.invalid/v1",
        vlm_api_key="fixture-only",
    )
    run = {
        "task_id": str(store.task_id),
        "run_id": str(uuid4()),
        "config": run_config(settings, batch),
    }
    path = tmp_path / "original.png"
    Image.new("RGB", (320, 240), "gray").save(path)
    requests = []

    def provider(request):
        requests.append(json.loads(request.content))
        payload = {
            "coordinate_space": "normalized_0_1000",
            "image_assessable": True,
            "full_image_audited": True,
            "summary": "fixture full-image audit",
            "candidates": []
            if score is None
            else [
                {
                    "index": 0,
                    "verdict": "reject",
                    "reason": "fixture conflict",
                    "proposed_label": None,
                    "evidence_strength": "clear",
                    "original_presence": "absent",
                }
            ],
            "additional_anomalies": [
                {
                    "label": "PL",
                    "bbox_xyxy": [700, 700, 900, 900],
                    "reason": "fixture localized crack",
                    "evidence_strength": "clear",
                }
            ],
        }
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(payload)},
                    }
                ]
            },
        )

    monkeypatch.setattr(
        "pipe_inspector.workflow.activities.OpenAICompatibleClient",
        lambda *args, **kwargs: OpenAICompatibleClient(
            *args, **kwargs, transport=httpx.MockTransport(provider)
        ),
    )
    activities = ImageActivities(
        settings, store, SimpleNamespace(path_for=lambda _: path), detector=batch
    )
    env = ActivityEnvironment()
    saved = await env.run(activities.verify_image, run)
    metadata = json.loads(requests[0]["messages"][1]["content"][0]["text"])
    assert metadata["prompt_version"] == "yolo-guided-review-v4"
    images = [c for c in requests[0]["messages"][1]["content"] if c["type"] == "image_url"]
    assert len(images) == (1 if score is None else 2)
    expected_predictions = (
        []
        if score is None
        else [{"label": "BX", "bbox_xyxy": [20, 30, 80, 100], "score": score}]
    )
    assert saved["fusion"]["predictions"] == expected_predictions
    assert saved["fusion"]["changes"][-1]["action"] == "add_pending"
    if score is not None:
        assert saved["fusion"]["changes"][0]["action"] == (
            "retain_high_confidence" if score == 0.95 else "retain_pending"
        )
    # Cached success must precede changed provider/default configuration checks.
    activities.settings = Settings(_env_file=None, image_review_mode="candidate_review")
    assert await env.run(activities.verify_image, run) == saved
    assert len(requests) == 1
    result = env.run(activities.finish_image, {"run": run})
    assert result["inspection_result"] == "inconclusive"
    assert result["runtime_status"] == "waiting_review"
    added = result["findings"][-1]
    assert (added["canonical_label"], added["source"], added["score"]) == ("PL", "vlm", None)
    assert added["review_status"] == "needs_review"
    if score is not None:
        original = result["findings"][0]
        assert original["canonical_label"] == "BX"
        assert original["review_status"] == "needs_review"


@pytest.mark.parametrize("mode", ["candidate_review", "independent_discovery"])
def test_legacy_pins_still_execute_after_admission_default_changes(mode):
    detector = SimpleNamespace(
        detector_id="fixture", model_version="v1", label_space_version="five"
    )
    old = run_config(Settings(_env_file=None, image_review_mode=mode), detector)
    current = ImageActivities(Settings(_env_file=None), Store(), None, detector=detector)
    current._check_versions(old, detector=True)


@pytest.mark.parametrize("tool", ["enhancement", "geometry"])
def test_optional_tools_keep_guided_low_confidence_rejection(tool):
    from pipe_inspector.agent.guided_review import GuidedReview
    from pipe_inspector.workflow.enhancement_activities import enhancement_baseline
    from pipe_inspector.workflow.geometry_activities import geometry_baseline

    store = Store()
    observation = ImageObservation.model_validate(
        {
            "detection": {
                "detector_id": "fixture",
                "model_version": "v1",
                "label_space_version": "five",
                "width": 100,
                "height": 100,
                "detections": [
                    {
                        "label": "FS",
                        "native_label": "FS",
                        "score": 0.4,
                        "bbox_xyxy": [10, 10, 30, 30],
                    }
                ],
            },
            "quality": {"mean_luminance": 100, "contrast": 30, "sharpness": 80, "adequate": True},
        }
    )
    review = GuidedReview.model_validate(
        {
            "image_assessable": True,
            "full_image_audited": True,
            "summary": "fixture",
            "candidates": [
                {
                    "index": 0,
                    "verdict": "reject",
                    "reason": "text overlay",
                    "evidence_strength": "clear",
                }
            ],
            "additional_anomalies": [],
        }
    )
    run = {"config": run_config(Settings(_env_file=None), observation.detection)}
    baseline = enhancement_baseline if tool == "enhancement" else geometry_baseline
    decision = baseline(store, run, store.task, observation, review)
    assert decision.findings[0].review_status == "needs_review"
    assert decision.inspection_result == "inconclusive"
