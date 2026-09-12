import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from PIL import Image
from pipe_inspector.contracts.enhancement import EnhancementOptions, EnhancementReview
from pipe_inspector.contracts.inspection import ImageReview
from pipe_inspector.media import MediaStore
from pipe_inspector.workflow.enhancement_config import build_enhancement_config
from pipe_inspector.workflow.image import ImageInspectionWorkflow
from pipe_inspector.workflow.supplement import ImageSupplementWorkflow
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment
from test_enhancement import Model
from test_image_workflow_faults import make_case


@pytest.fixture
def case(tmp_path, monkeypatch):
    from pipe_inspector.workflow.enhancement_activities import EnhancementActivities

    base, store, run, detector = make_case(monkeypatch)
    media = MediaStore(tmp_path / "assets")
    stream = io.BytesIO()
    Image.new("RGB", (32, 24), "gray").save(stream, "PNG")
    stream.seek(0)
    asset = media.ingest(stream)
    store.task.input_asset = store.asset = asset.ref
    store.get_asset = lambda ref: asset
    base.media = media
    settings = base.settings.model_copy(update={"enhancement_models_dir": tmp_path})
    options = EnhancementOptions(kind="dehaze")
    pin = build_enhancement_config(settings, options, check_files=False)
    run["config"]["enhancement"] = pin
    run["config"]["human_review_version"] = "human-review-v1"
    model = Model("dehaze")
    model.model_id, model.model_version = pin["model_id"], pin["model_version"]
    factory = Mock(return_value=model)
    reviewer = SimpleNamespace(
        review=AsyncMock(
            return_value=EnhancementReview(
                quality_effect="unchanged",
                summary="不能视为准确率证明",
            )
        )
    )
    enhanced = EnhancementActivities(
        settings, store, media, model_factory=factory, reviewer=reviewer
    )
    env = ActivityEnvironment()
    env.run(base.detect_image, run)
    store.steps["verify"] = {
        "review": ImageReview(
            image_assessable=True,
            full_image_audited=True,
            candidates=(),
            additional_anomalies=(),
            summary="无明显异常",
        ).model_dump(mode="json")
    }
    store.save_enhancement = lambda task_id, result, call: store.save_step(
        task_id,
        "enhance",
        result.model_dump(mode="json"),
        call,
    )
    return enhanced, base, store, run, factory, env


def test_enhance_commit_ack_loss_reuses_saved_asset_and_one_model_call(case):
    enhanced, _, store, run, factory, env = case
    env.run(enhanced.prepare_image_enhancement, run)
    save = store.save_enhancement

    def lose_ack(*args):
        save(*args)
        raise RuntimeError("commit acknowledgement lost")

    store.save_enhancement = lose_ack
    with pytest.raises(RuntimeError, match="acknowledgement"):
        env.run(enhanced.enhance_image, run)
    cached = env.run(enhanced.enhance_image, run)
    assert cached == store.steps["enhance"]
    assert factory.call_count == 1
    assert store.task.snapshot.stage == "tools"
    assert store.task.snapshot.active_tool == "dehaze"


@pytest.mark.asyncio
async def test_aux_review_is_cached_and_final_failure_cannot_be_completed_by_human(case):
    enhanced, base, store, run, factory, env = case
    env.run(enhanced.prepare_image_enhancement, run)
    env.run(enhanced.enhance_image, run)
    await env.run(enhanced.review_image_enhancement, run)
    await env.run(enhanced.review_image_enhancement, run)
    assert enhanced.reviewer.review.call_count == 1
    store.steps.pop("enhancement_review")
    result = env.run(base.finish_image, {"run": run, "error": "authentication_failed"})
    assert result["runtime_status"] == "partial"
    assert result["inspection_result"] == "inconclusive"
    assert "authentication_failed" in result["waiting_reason"]


def test_model_pin_drift_fails_before_inference(case):
    enhanced, _, store, run, factory, env = case
    env.run(enhanced.prepare_image_enhancement, run)
    run["config"]["enhancement"]["model_version"] = "different.pth"
    with pytest.raises(ApplicationError) as caught:
        env.run(enhanced.enhance_image, run)
    assert caught.value.type == "configuration_changed"
    assert caught.value.non_retryable
    factory.assert_not_called()


@pytest.mark.parametrize("linked", [False, True])
@pytest.mark.asyncio
async def test_workflow_order_and_gpu_queue_are_explicit(monkeypatch, linked):
    calls = []

    async def execute(name, payload, **options):
        calls.append((name, options))
        return {"runtime_status": "completed"} if name.startswith("finish_") else {}

    monkeypatch.setattr("pipe_inspector.workflow.image.workflow.execute_activity", execute)
    workflow = ImageSupplementWorkflow() if linked else ImageInspectionWorkflow()
    await workflow.run(
        {"config": {"enhancement": {}, "gpu_queue": "serial-gpu", "api_timeout_seconds": 5}}
    )
    names = [name for name, _ in calls]
    expected = ["prepare_image_enhancement", "enhance_image", "review_image_enhancement"]
    assert names == (
        expected + ["finish_image_supplement"]
        if linked
        else [
            "publish_image_stage",
            "detect_image",
            "verify_image",
            *expected,
            "finish_image",
        ]
    )
    assert dict(calls)["enhance_image"]["task_queue"] == "serial-gpu"
    assert dict(calls)["enhance_image"]["retry_policy"].maximum_attempts == 3
