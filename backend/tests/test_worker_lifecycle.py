"""Worker readiness follows startup; termination drains instead of cancelling workflows."""

import asyncio
import json
import os
import signal

import pytest
from test_video_bootstrap import load_script


@pytest.fixture
async def worker_signals(monkeypatch):
    loop = asyncio.get_running_loop()
    handlers = {}
    monkeypatch.setattr(loop, "add_signal_handler", lambda sig, fn: handlers.__setitem__(sig, fn))
    monkeypatch.setattr(loop, "remove_signal_handler", lambda sig: handlers.pop(sig, None))
    return handlers


@pytest.mark.asyncio
async def test_signal_drains_running_worker_and_removes_its_ready_marker(tmp_path, worker_signals):
    module = load_script("inspection_worker")
    ready = tmp_path / "cpu.ready.json"
    completed = asyncio.Event()
    shutdowns = []

    class Worker:
        is_running = False

        async def run(self):
            self.is_running = True
            await completed.wait()

        async def shutdown(self):
            shutdowns.append(True)
            completed.set()

    running = asyncio.create_task(module.run_worker(Worker(), "cpu", ready))
    try:
        async with asyncio.timeout(2):
            while not ready.exists():
                await asyncio.sleep(0.01)
        assert json.loads(ready.read_text()) == {"pid": os.getpid(), "kind": "cpu"}
        assert ready.stat().st_mode & 0o777 == 0o600
        worker_signals[signal.SIGTERM]()
        await asyncio.wait_for(running, timeout=2)
        assert shutdowns == [True]
        assert not ready.exists()
        assert not worker_signals
    finally:
        if not running.done():
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)


@pytest.mark.asyncio
async def test_worker_start_failure_never_publishes_ready(tmp_path, worker_signals):
    module = load_script("inspection_worker")
    ready = tmp_path / "gpu.ready.json"

    class Worker:
        is_running = False

        async def run(self):
            raise RuntimeError("namespace unavailable")

    with pytest.raises(RuntimeError, match="namespace unavailable"):
        await module.run_worker(Worker(), "gpu", ready)
    assert not ready.exists()
    assert not worker_signals
