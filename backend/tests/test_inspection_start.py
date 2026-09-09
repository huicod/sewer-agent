from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pipe_inspector.config import Settings
from pipe_inspector.workflow.start import InspectionStarter, InspectionStartError, run_config
from temporalio.common import WorkflowIDReusePolicy


class MemoryStore:
    def __init__(self, kind="image"):
        self.task = SimpleNamespace(
            input_asset=object(), snapshot=SimpleNamespace(task_id=uuid4(), is_terminal=False)
        )
        self.kind = kind
        self.run = None

    def get_task(self, task_id):
        return self.task

    def get_asset(self, ref):
        return SimpleNamespace(kind=self.kind)

    def get_run(self, task_id):
        return self.run

    def ensure_run(self, task_id, config):
        if self.run is None:
            self.run = {"task_id": str(task_id), "run_id": str(uuid4()), "config": config}
        return self.run


@pytest.mark.asyncio
async def test_start_pins_config_and_reuses_workflow_identity_after_ambiguous_failure():
    settings = Settings(_env_file=None, vlm_model="fixture-model")
    store = MemoryStore()
    client = SimpleNamespace(start_workflow=AsyncMock(side_effect=[OSError(), object()]))
    connect = AsyncMock(return_value=client)
    starter = InspectionStarter(settings, store, connect=connect)
    task_id = store.task.snapshot.task_id
    with pytest.raises(InspectionStartError) as error:
        await starter.start(task_id)
    assert error.value.code == "workflow_unavailable"
    original = store.run.copy()
    assert await starter.start(task_id) == store.task
    assert store.run == original
    assert client.start_workflow.call_count == 2
    first, second = client.start_workflow.call_args_list
    assert first == second
    assert first.kwargs["id"] == f"inspection:{task_id}"
    assert first.kwargs["id_reuse_policy"] == WorkflowIDReusePolicy.REJECT_DUPLICATE


@pytest.mark.asyncio
async def test_video_does_not_create_run_or_connect():
    connect = AsyncMock()
    store = MemoryStore("video")
    with pytest.raises(InspectionStartError) as error:
        await InspectionStarter(Settings(_env_file=None), store, connect=connect).start(uuid4())
    assert error.value.code == "image_only"
    assert store.run is None
    connect.assert_not_called()


def test_configuration_contains_versions_but_never_credentials():
    settings = Settings(_env_file=None, vlm_api_key="private-key", vlm_model="fixture")
    config = run_config(settings)
    assert config["detector_id"] == "ultralytics-yolo26"
    assert config["vlm_model"] == "fixture"
    assert "private-key" not in str(config)
    assert "api_key" not in str(config)
    assert config["policy_version"]


@pytest.mark.parametrize(
    "url",
    [
        "https://user:fixture-secret@example.invalid/v1",
        "https://example.invalid/v1?api_key=fixture-secret",
        "https://example.invalid/v1#fixture-secret",
    ],
)
def test_provider_url_cannot_persist_embedded_credentials(url):
    with pytest.raises(InspectionStartError) as error:
        run_config(Settings(_env_file=None, vlm_base_url=url))
    assert error.value.code == "invalid_vlm_configuration"
    assert "fixture-secret" not in str(error.value)
