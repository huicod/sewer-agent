from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pipe_inspector.api.app import create_app
from pipe_inspector.config import Settings
from pipe_inspector.contracts.assets import AssetRecord, AssetRef
from pipe_inspector.contracts.common import ImageRegion
from pipe_inspector.contracts.geometry import (
    GeometryMeasurements,
    RelativeDepthResult,
    RelativeDepthSummary,
    SegmentationResult,
)
from pipe_inspector.contracts.intake import TaskRecord
from pipe_inspector.contracts.tasks import TaskSnapshot


def _asset(kind, media_type, source, *, width=8, height=6):
    return AssetRecord(
        ref=AssetRef(asset_id=uuid4()),
        kind=kind,
        media_type=media_type,
        byte_size=128,
        width=width,
        height=height,
        parent=source,
    )


def geometry_fixture():
    task_id = uuid4()
    source = AssetRef(asset_id=uuid4())
    task = TaskRecord(
        request_id=uuid4(),
        session_id=uuid4(),
        input_asset=source,
        instruction="量化这个发现区域",
        created_at=datetime.now(timezone.utc),
        snapshot=TaskSnapshot(
            task_id=task_id,
            updated_at=datetime.now(timezone.utc),
            runtime_status="partial",
            inspection_result="inconclusive",
            stage="finished",
        ),
    )
    prompt = ImageRegion(width=8, height=6, bbox_xyxy=(2, 1, 7, 5))
    segmentation = SegmentationResult(
        input_asset=source,
        prompt=prompt,
        mask_asset=_asset("mask", "image/png", source),
        overlay_asset=_asset("mask", "image/png", source),
        model_id="facebook/sam2.1-hiera-large",
        model_version="sam-pinned-v1",
        model_score=0.81,
        area_pixels=6,
        mask_region=ImageRegion(width=8, height=6, bbox_xyxy=(3, 2, 6, 4)),
    )
    depth = RelativeDepthResult(
        input_asset=source,
        raw_asset=_asset("depth_map", "application/x-npy", source),
        preview_asset=_asset("depth_map", "image/png", source),
        model_id="depth-anything/Depth-Anything-V2-Small-hf",
        model_version="depth-pinned-v1",
        larger_is="nearer",
        raw_min=0,
        raw_max=7,
    )
    mask_measurement = GeometryMeasurements(
        input_asset=source,
        area_pixels=6,
        image_plane_fraction=0.125,
        bbox_width_pixels=3,
        bbox_height_pixels=2,
    )
    depth_measurement = GeometryMeasurements(
        input_asset=source,
        area_pixels=6,
        image_plane_fraction=0.125,
        bbox_width_pixels=3,
        bbox_height_pixels=2,
        relative_depth=RelativeDepthSummary(
            q10=3,
            median=4,
            q90=5,
            larger_is="nearer",
        ),
    )
    return task, segmentation, depth, mask_measurement, depth_measurement


def test_loader_validates_checkpoint_envelopes_and_prefers_depth_measurement():
    from pipe_inspector.api.inspection import load_geometry_detail

    task, segmentation, depth, mask_measurement, depth_measurement = geometry_fixture()
    reads = []

    def load_step(task_id, step):
        reads.append((task_id, step))
        return {
            "geometry_segment": {
                "result": segmentation.model_dump(mode="json"),
                "measurements": mask_measurement.model_dump(mode="json"),
            },
            "geometry_depth": {
                "result": depth.model_dump(mode="json"),
                "measurements": depth_measurement.model_dump(mode="json"),
            },
        }[step]

    detail = load_geometry_detail(SimpleNamespace(load_step=load_step), task)

    assert detail.task_id == task.snapshot.task_id
    assert detail.segmentation == segmentation
    assert detail.depth == depth
    assert detail.measurements == depth_measurement
    assert reads == [
        (task.snapshot.task_id, "geometry_segment"),
        (task.snapshot.task_id, "geometry_depth"),
    ]


def test_loader_rejects_wrong_source_and_invalid_checkpoint_shape():
    from pipe_inspector.api.inspection import load_geometry_detail

    task, segmentation, depth, _, _ = geometry_fixture()
    unrelated = AssetRef(asset_id=uuid4())
    depth_payload = depth.model_dump(mode="json")
    depth_payload["input_asset"] = unrelated.model_dump(mode="json")
    for field in ("raw_asset", "preview_asset"):
        depth_payload[field]["parent"] = unrelated.model_dump(mode="json")
    repo = SimpleNamespace(
        load_step=lambda _, step: (
            {"result": segmentation.model_dump(mode="json"), "measurements": None}
            if step == "geometry_segment"
            else {"result": depth_payload, "measurements": None}
        )
    )
    with pytest.raises(ValueError, match="requested task"):
        load_geometry_detail(repo, task)

    invalid = SimpleNamespace(
        load_step=lambda _, step: (
            {
                "result": segmentation.model_dump(mode="json"),
                "measurements": None,
                "path": "/private",
            }
            if step == "geometry_segment"
            else None
        )
    )
    with pytest.raises(ValueError, match="checkpoint"):
        load_geometry_detail(invalid, task)


async def test_geometry_endpoint_is_read_only_and_old_task_returns_explicit_nulls(tmp_path):
    task, *_ = geometry_fixture()
    reads = []
    repo = SimpleNamespace(
        get_task=lambda task_id: (reads.append(("task", task_id)) or task),
        load_step=lambda task_id, step: (reads.append((step, task_id)) or None),
    )
    app = create_app(Settings(_env_file=None, asset_root=tmp_path), repository=repo)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/tasks/{task.snapshot.task_id}/geometry")

    assert response.status_code == 200
    assert response.json() == {
        "task_id": str(task.snapshot.task_id),
        "segmentation": None,
        "depth": None,
        "measurements": None,
    }
    assert reads == [
        ("task", task.snapshot.task_id),
        ("geometry_segment", task.snapshot.task_id),
        ("geometry_depth", task.snapshot.task_id),
    ]


async def test_geometry_endpoint_returns_only_validated_public_evidence(tmp_path):
    task, segmentation, depth, _, depth_measurement = geometry_fixture()

    def load_step(_, step):
        return {
            "geometry_segment": {
                "result": segmentation.model_dump(mode="json"),
                "measurements": None,
            },
            "geometry_depth": {
                "result": depth.model_dump(mode="json"),
                "measurements": depth_measurement.model_dump(mode="json"),
            },
        }[step]

    repo = SimpleNamespace(get_task=lambda _: task, load_step=load_step)
    app = create_app(Settings(_env_file=None, asset_root=tmp_path), repository=repo)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/tasks/{task.snapshot.task_id}/geometry")

    assert response.status_code == 200
    payload = response.json()
    assert payload["segmentation"] == segmentation.model_dump(mode="json")
    assert payload["depth"] == depth.model_dump(mode="json")
    assert payload["measurements"] == depth_measurement.model_dump(mode="json")
    assert "checkpoint" not in response.text
    assert "/private" not in response.text
