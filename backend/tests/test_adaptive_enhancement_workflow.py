from types import SimpleNamespace
from uuid import uuid4

import pytest
from pipe_inspector.agent.api_client import APIClientError
from temporalio.exceptions import ApplicationError
from test_adaptive_enhancement import proposal
from test_enhancement_workflow import case as case


def automatic(case, *, suggestion=None, failure=None, no_models=False):
    from pipe_inspector.workflow.enhancement_selection import EnhancementSelectionActivities

    enhanced, base, store, run, factory, env = case
    pin = run["config"].pop("enhancement")
    run["config"]["auto_enhancement"] = {
        "version": "enhancement-selection-v1",
        "call_id": str(uuid4()),
        "models": {} if no_models else {"dehaze": pin | {"options": None}},
    }
    requests = []

    async def propose(*args, **kwargs):
        requests.append(kwargs)
        if failure:
            raise failure
        return suggestion or proposal(options={"kind": "dehaze"}, degradation="haze")

    selector = EnhancementSelectionActivities(
        enhanced.settings, store, enhanced.media, selector=SimpleNamespace(propose=propose)
    )
    return selector, run, requests


@pytest.mark.asyncio
async def test_selection_ack_loss_and_worker_replacement_use_same_choice(case):
    selector, run, requests = automatic(case)
    enhanced, base, store, _, factory, env = case
    save = store.save_step

    def lose_ack(task_id, step, payload, *args):
        result = save(task_id, step, payload, *args)
        if step == "enhancement_selection":
            raise RuntimeError("commit ack lost")
        return result

    store.save_step = lose_ack
    with pytest.raises(RuntimeError, match="ack lost"):
        await env.run(selector.select_image_enhancement, run)
    store.save_step = save
    selector.selector = None  # restart without test/API client: cached choice must suffice
    picked = await env.run(selector.select_image_enhancement, run)
    assert len(requests) == 1 and picked["enhancement"]["model_id"] == "dehazeformer-mct"
    assert set(store.steps["enhancement_selection"]) == {"selection"}
    selected_run = run | {"config": run["config"] | {"enhancement": picked["enhancement"]}}
    env.run(enhanced.prepare_image_enhancement, selected_run)
    env.run(enhanced.enhance_image, selected_run)
    await env.run(enhanced.review_image_enhancement, selected_run)
    assert store.steps["enhance"]["kind"] == "dehaze"
    assert factory.call_count == 1


@pytest.mark.asyncio
async def test_no_deployed_model_skips_without_api_or_gpu(case):
    selector, run, requests = automatic(case, no_models=True)
    _, base, store, _, factory, env = case
    result = await env.run(selector.select_image_enhancement, run)
    assert result["enhancement"] is None
    assert result["selection"]["reason"] == "no_available_models"
    assert requests == [] and factory.call_count == 0
    assert not any(call.step_id == "enhancement_selection" for call in store.calls)


@pytest.mark.asyncio
async def test_selection_failure_preserves_baseline_and_is_not_completable_human_gate(case):
    selector, run, requests = automatic(
        case, failure=APIClientError("authentication_failed", retryable=False)
    )
    _, base, store, _, factory, env = case
    with pytest.raises(ApplicationError) as error:
        await env.run(selector.select_image_enhancement, run)
    result = env.run(base.finish_image, {"run": run, "error": error.value.type})
    assert result["runtime_status"] == "partial"
    assert result["inspection_result"] == "inconclusive"
    assert "enhancement_selection" not in store.steps and factory.call_count == 0
    assert any(
        call.step_id == "enhancement_selection" and call.status == "failed" for call in store.calls
    )


@pytest.mark.asyncio
async def test_uncertain_selection_saves_reason_but_does_not_run_enhancement(case):
    selector, run, requests = automatic(case, suggestion=proposal(confidence="uncertain"))
    _, _, store, _, factory, env = case
    result = await env.run(selector.select_image_enhancement, run)
    assert result["enhancement"] is None
    assert result["selection"]["reason"] == "evidence_uncertain"
    assert store.steps["enhancement_selection"]["selection"]["status"] == "skipped"
    assert factory.call_count == 0


def test_selector_failure_keeps_successful_original_review_confirmations(monkeypatch):
    from pipe_inspector.contracts.detection import Detection
    from pipe_inspector.contracts.inspection import ImageReview
    from temporalio.testing import ActivityEnvironment
    from test_image_workflow_faults import make_case

    original, store, run, _ = make_case(
        monkeypatch,
        detections=(Detection(label="AJ", native_label="AJ", score=0.9, bbox_xyxy=(2, 3, 18, 20)),),
    )
    run["config"]["auto_enhancement"] = {"version": "enhancement-selection-v1"}
    env = ActivityEnvironment()
    env.run(original.detect_image, run)
    store.steps["verify"] = {
        "review": ImageReview(
            image_assessable=True,
            full_image_audited=True,
            candidates=({"index": 0, "verdict": "accept", "reason": "接口边缘清楚"},),
            additional_anomalies=(),
            summary="原图确认支管暗接",
        ).model_dump(mode="json"),
        "skipped": False,
    }
    result = env.run(original.finish_image, {"run": run, "error": "authentication_failed"})
    assert result["runtime_status"] == "partial"
    assert result["inspection_result"] == "defects_found"
    finding = result["findings"][0]
    assert finding["review_status"] == "confirmed"
    assert finding["canonical_label"] == "AJ" and finding["score"] == 0.9
    assert finding["evidence"][0]["region"]["bbox_xyxy"] == [2, 3, 18, 20]
    assert "authentication_failed" in result["waiting_reason"]
