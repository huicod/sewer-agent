"""Online legacy handlers return evidence, never perform new supplementation."""

from uuid import uuid4

import pytest
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment


class Checkpoints:
    def __init__(self, task_id, steps):
        self.task_id, self.steps = task_id, steps

    def load_step(self, task_id, step):
        assert task_id == self.task_id
        return self.steps.get(step)


@pytest.mark.parametrize("step", ["selection", "scan"])
@pytest.mark.parametrize("cached", [False, True])
def test_retired_handlers_only_return_existing_checkpoints(step, cached):
    from pipe_inspector.workflow.retired_open_scan import RetiredOpenScanActivities

    task_id = uuid4()
    plan = {"selected": [{"frame_index": None}], "incomplete": False}
    checkpoints = {"open_plan": plan, "open_scan:0": {"batch": {"candidates": [{}]}}}
    repo = Checkpoints(task_id, checkpoints if cached else {})
    handlers = RetiredOpenScanActivities(repo)
    run = {"task_id": str(task_id), "config": {"open_scan": {"version": "open-scan-v1"}}}
    function, payload = (
        (handlers.select_open_scan, run)
        if step == "selection"
        else (handlers.scan_open_candidate, {"run": run, "index": 0})
    )
    env = ActivityEnvironment()
    if cached:
        result = env.run(function, payload)
        assert result == (plan if step == "selection" else {"index": 0, "candidate_count": 1})
        assert repo.steps == checkpoints
    else:
        with pytest.raises(ApplicationError) as error:
            env.run(function, payload)
        assert error.value.type == "tool_retired"
        assert error.value.non_retryable
        assert repo.steps == {}


@pytest.mark.parametrize("kind", ["cpu", "gpu"])
async def test_production_worker_registers_only_checkpoint_readers(monkeypatch, kind):
    import importlib.util
    from pathlib import Path
    from unittest.mock import AsyncMock

    from pipe_inspector.config import Settings

    spec = importlib.util.spec_from_file_location(
        "retirement_worker_test",
        Path(__file__).resolve().parents[2] / "scripts/inspection_worker.py",
    )
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    settings = Settings(
        _env_file=None, open_detector_path="open.pt", open_text_encoder_path="encoder.ts"
    )
    task_id = uuid4()
    repo = Checkpoints(task_id, {})
    monkeypatch.setattr(worker, "Settings", lambda: settings)
    monkeypatch.setattr(worker, "HumanReviewRepository", lambda *_: repo)
    monkeypatch.setattr(worker.Client, "connect", AsyncMock())
    executed = []

    class Worker:
        is_running = False

        def __init__(self, client, **options):
            self.options, self.task_queue = options, options["task_queue"]

        async def run(self):
            name = "select_open_scan" if kind == "cpu" else "scan_open_candidate"
            handler = next(a for a in self.options["activities"] if a.__name__ == name)
            run = {"task_id": str(task_id), "config": {"open_scan": {"version": "open-scan-v1"}}}
            payload = run if kind == "cpu" else {"run": run, "index": 0}
            with pytest.raises(ApplicationError) as error:
                ActivityEnvironment().run(handler, payload)
            assert error.value.type == "tool_retired"
            assert error.value.non_retryable
            executed.append(name)

    monkeypatch.setattr(worker, "Worker", Worker)
    await worker.serve(kind)
    assert executed == ["select_open_scan" if kind == "cpu" else "scan_open_candidate"]
    assert not repo.steps
