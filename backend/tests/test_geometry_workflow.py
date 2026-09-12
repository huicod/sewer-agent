from datetime import timedelta

import pytest
from pipe_inspector.workflow.image import ImageInspectionWorkflow
from pipe_inspector.workflow.supplement import ImageSupplementWorkflow


@pytest.mark.asyncio
@pytest.mark.parametrize("linked", [False, True])
@pytest.mark.parametrize(
    ("operation", "geometry_steps"),
    [
        ("segment", ["prepare_image_geometry", "segment_image"]),
        ("relative_depth", ["prepare_image_geometry", "estimate_relative_depth"]),
        (
            "relative_measure",
            ["prepare_image_geometry", "segment_image", "estimate_relative_depth"],
        ),
    ],
)
async def test_geometry_workflow_orders_cpu_and_serial_gpu_steps(
    monkeypatch, linked, operation, geometry_steps
):
    calls = []

    async def execute(name, payload, **options):
        calls.append((name, options))
        return {"runtime_status": "completed"} if name.startswith("finish_") else {}

    module = "pipe_inspector.workflow.supplement" if linked else "pipe_inspector.workflow.image"
    monkeypatch.setattr(f"{module}.workflow.execute_activity", execute)
    workflow = ImageSupplementWorkflow() if linked else ImageInspectionWorkflow()
    run = {
        "config": {
            "geometry": {"options": {"operation": operation}},
            "gpu_queue": "serial-gpu",
            "api_timeout_seconds": 5,
        }
    }

    await workflow.run(run)

    names = [name for name, _ in calls]
    assert names == (
        geometry_steps + ["finish_image_supplement"]
        if linked
        else [
            "publish_image_stage",
            "detect_image",
            "verify_image",
            *geometry_steps,
            "finish_image",
        ]
    )
    options = dict(calls)
    for step in {"segment_image", "estimate_relative_depth"} & set(geometry_steps):
        assert options[step]["task_queue"] == "serial-gpu"
        assert options[step]["start_to_close_timeout"] == timedelta(seconds=180)
        assert options[step]["retry_policy"].maximum_attempts == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("linked", [False, True])
async def test_legacy_workflow_history_adds_no_geometry_commands(monkeypatch, linked):
    calls = []

    async def execute(name, payload, **options):
        calls.append(name)
        return {"runtime_status": "completed"} if name.startswith("finish_") else {}

    module = "pipe_inspector.workflow.supplement" if linked else "pipe_inspector.workflow.image"
    monkeypatch.setattr(f"{module}.workflow.execute_activity", execute)
    workflow = ImageSupplementWorkflow() if linked else ImageInspectionWorkflow()
    await workflow.run({"config": {"gpu_queue": "gpu", "api_timeout_seconds": 5}})

    assert not any("geometry" in name or name == "segment_image" for name in calls)
    assert "estimate_relative_depth" not in calls
