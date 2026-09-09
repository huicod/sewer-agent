from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pipe_inspector.config import Settings
from pipe_inspector.storage.repository import SubmissionConflict
from pipe_inspector.workflow.start import InspectionStarter, run_config
from temporalio.exceptions import WorkflowAlreadyStartedError
from test_inspection_start import MemoryStore


@pytest.mark.asyncio
async def test_concurrent_start_uses_first_persisted_run():
    settings = Settings(_env_file=None)
    store = MemoryStore()
    existing = {
        "task_id": str(store.task.snapshot.task_id),
        "run_id": str(uuid4()),
        "config": run_config(settings),
    }

    def race(task_id, config):
        store.run = existing
        raise SubmissionConflict

    store.ensure_run = race
    client = AsyncMock()
    result = await InspectionStarter(settings, store, connect=AsyncMock(return_value=client)).start(
        store.task.snapshot.task_id
    )
    assert result == store.task
    assert client.start_workflow.call_args.args[1] == existing


@pytest.mark.asyncio
async def test_already_started_is_a_successful_reconciliation():
    store = MemoryStore()
    client = AsyncMock()
    client.start_workflow.side_effect = WorkflowAlreadyStartedError(
        "existing", "ImageInspectionWorkflow"
    )
    result = await InspectionStarter(
        Settings(_env_file=None), store, connect=AsyncMock(return_value=client)
    ).start(store.task.snapshot.task_id)
    assert result == store.task
