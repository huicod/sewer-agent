import asyncio
from uuid import uuid4

import pytest
from pipe_inspector.contracts.detection import Detection
from pipe_inspector.contracts.tasks import TaskSnapshot
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment
from test_image_workflow_faults import make_case


def test_new_image_finish_waits_without_an_active_tool(monkeypatch):
    activities, store, run, _ = make_case(
        monkeypatch,
        detections=(
            Detection(label="BX", native_label="BX", score=0.94, bbox_xyxy=(2, 3, 18, 20)),
        ),
    )
    run["config"]["human_review_version"] = "human-review-v1"
    env = ActivityEnvironment()
    env.run(activities.detect_image, run)
    result = env.run(activities.finish_image, {"run": run, "error": "review_failed"})
    assert result["runtime_status"] == "waiting_review"
    assert result["stage"] == "human_review"
    assert result["overall_progress"] == 0.95
    assert result["can_cancel"] is True
    assert result["active_tool"] is None
    assert store.task.snapshot.is_terminal is False


def test_pinless_image_still_finishes_partial(monkeypatch):
    activities, _, run, _ = make_case(monkeypatch)
    run["config"].pop("human_review_version", None)
    env = ActivityEnvironment()
    env.run(activities.detect_image, run)
    result = env.run(activities.finish_image, {"run": run, "error": "review_failed"})
    assert result["runtime_status"] == "partial"


def test_repeated_automated_finish_cannot_replace_human_terminal_snapshot(monkeypatch):
    activities, store, run, _ = make_case(monkeypatch)
    run["config"]["human_review_version"] = "human-review-v1"
    env = ActivityEnvironment()
    env.run(activities.detect_image, run)
    automated = env.run(activities.finish_image, {"run": run, "error": "review_failed"})
    manual = TaskSnapshot.model_validate(
        automated
        | {
            "runtime_status": "partial",
            "stage": "finished",
            "can_cancel": False,
            "overall_progress": 1,
            "waiting_reason": "human reviewed: image remains unclear",
        }
    )
    store.update_snapshot(manual)
    result = env.run(activities.finish_image, {"run": run})
    assert result == manual.model_dump(mode="json")
    assert store.task.snapshot == manual


async def _wait_condition(predicate):
    while not predicate():
        await asyncio.sleep(0)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["image", "supplement", "video"])
async def test_inspection_workflows_enter_review_gate(monkeypatch, kind):
    from pipe_inspector.workflow.image import ImageInspectionWorkflow
    from pipe_inspector.workflow.supplement import ImageSupplementWorkflow
    from pipe_inspector.workflow.video import VideoInspectionWorkflow

    classes = {
        "image": ImageInspectionWorkflow,
        "supplement": ImageSupplementWorkflow,
        "video": VideoInspectionWorkflow,
    }
    calls = []

    async def execute(name, payload, **options):
        if name.startswith("finish_"):
            return {"runtime_status": "waiting_review"}
        return {"review_frames": []}

    async def wait(gate, run):
        calls.append(run)
        return {"runtime_status": "completed"}

    monkeypatch.setattr(f"pipe_inspector.workflow.{kind}.workflow.execute_activity", execute)
    monkeypatch.setattr("pipe_inspector.workflow.human_review.HumanReviewGate.wait", wait)
    if kind == "video":

        async def video_execute(self, name, payload, **options):
            return await execute(name, payload, **options)

        monkeypatch.setattr(VideoInspectionWorkflow, "_execute", video_execute)
    instance = classes[kind]()
    run = {
        "task_id": str(uuid4()),
        "config": {
            "human_review_version": "human-review-v1",
            "gpu_queue": "fixture",
            "api_timeout_seconds": 1,
        },
    }
    assert (await instance.run(run))["runtime_status"] == "completed"
    assert calls == [run]
    assert instance.current_stage() == "finished"


@pytest.mark.asyncio
async def test_gate_waits_then_applies_once_and_refuses_competing_cancel(monkeypatch):
    from pipe_inspector.workflow.human_review import HumanReviewGate

    task_id, request_id = str(uuid4()), str(uuid4())
    run = {"task_id": task_id, "config": {"human_review_version": "human-review-v1"}}
    payload = {"request_id": request_id}
    receipt = dict(
        task_id=task_id,
        request_id=request_id,
        runtime_status="completed",
        inspection_result="defects_found",
    )
    started, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def execute(name, value, **options):
        calls.append((name, value))
        started.set()
        await release.wait()
        return receipt

    monkeypatch.setattr(
        "pipe_inspector.workflow.human_review.workflow.wait_condition", _wait_condition
    )
    monkeypatch.setattr(
        "pipe_inspector.workflow.human_review.workflow.all_handlers_finished", lambda: True
    )
    monkeypatch.setattr("pipe_inspector.workflow.human_review.workflow.execute_activity", execute)
    gate = HumanReviewGate()
    waiting = asyncio.create_task(gate.wait(run))
    await asyncio.sleep(0)
    assert not waiting.done()
    submit = asyncio.create_task(gate.submit(payload))
    await started.wait()
    assert gate.request_cancel() is False
    duplicate = asyncio.create_task(gate.submit(payload.copy()))
    release.set()
    assert await submit == await duplicate == receipt
    assert await waiting == receipt
    assert calls == [("apply_human_review", {"task_id": task_id, "submission": payload})]
    with pytest.raises(ApplicationError):
        await gate.submit({"request_id": str(uuid4())})


@pytest.mark.asyncio
async def test_cancel_waiting_gate_prevents_review_submission(monkeypatch):
    from pipe_inspector.workflow.human_review import HumanReviewGate

    task_id = str(uuid4())
    run = {"task_id": task_id, "config": {"human_review_version": "human-review-v1"}}
    calls = []

    async def execute(name, payload, **options):
        calls.append((name, payload))
        return {"task_id": task_id, "runtime_status": "cancelled"}

    monkeypatch.setattr(
        "pipe_inspector.workflow.human_review.workflow.wait_condition", _wait_condition
    )
    monkeypatch.setattr(
        "pipe_inspector.workflow.human_review.workflow.all_handlers_finished", lambda: True
    )
    monkeypatch.setattr("pipe_inspector.workflow.human_review.workflow.execute_activity", execute)
    gate = HumanReviewGate()
    assert gate.request_cancel() is False
    waiting = asyncio.create_task(gate.wait(run))
    await asyncio.sleep(0)
    assert gate.request_cancel() is True
    with pytest.raises(ApplicationError):
        await gate.submit({"request_id": str(uuid4())})
    assert (await waiting)["runtime_status"] == "cancelled"
    assert calls == [("cancel_human_review", {"task_id": task_id})]


@pytest.mark.asyncio
async def test_duplicate_waiters_stay_serial_after_first_apply_fails(monkeypatch):
    from pipe_inspector.workflow.human_review import HumanReviewGate

    monkeypatch.setattr(
        "pipe_inspector.workflow.human_review.workflow.wait_condition", _wait_condition
    )
    gate = HumanReviewGate()
    gate.waiting = True
    gate.run = {"task_id": str(uuid4())}
    active, maximum, count = 0, 0, 0
    release = asyncio.Event()

    async def execute(*args, **kwargs):
        nonlocal active, maximum, count
        count += 1
        attempt = count
        active += 1
        maximum = max(maximum, active)
        try:
            await release.wait()
            await asyncio.sleep(0)
            if attempt == 1:
                raise ApplicationError("fixture rejected decision")
            return {"runtime_status": "completed"}
        finally:
            active -= 1

    monkeypatch.setattr("pipe_inspector.workflow.human_review.workflow.execute_activity", execute)
    payload = {"request_id": str(uuid4())}
    submissions = [asyncio.create_task(gate.submit(payload)) for _ in range(3)]
    await asyncio.sleep(0)
    release.set()
    results = await asyncio.gather(*submissions, return_exceptions=True)
    assert isinstance(results[0], ApplicationError)
    assert results[1] == results[2] == {"runtime_status": "completed"}
    assert count == 2 and maximum == 1
