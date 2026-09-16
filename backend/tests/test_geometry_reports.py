from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pipe_inspector.api.app import create_app
from pipe_inspector.config import Settings
from pipe_inspector.contracts.geometry import GeometryDetail
from pipe_inspector.contracts.reports import TaskReport
from pipe_inspector.contracts.tools import ToolCall
from pipe_inspector.reports import render_html
from test_geometry_api import geometry_fixture


def _call(task, segmentation, depth):
    return ToolCall(
        call_id=uuid4(),
        task_id=task.snapshot.task_id,
        step_id="geometry_depth",
        idempotency_key="geometry-depth-once",
        tool_name="relative_measure",
        tool_version="geometry-online-v1",
        model_version=depth.model_version,
        resource="gpu",
        timeout_seconds=180,
        input_assets=(task.input_asset,),
        output_assets=(segmentation.mask_asset.ref, depth.raw_asset.ref),
        status="succeeded",
    )


def test_report_contract_requires_geometry_to_belong_to_task_and_source():
    task, segmentation, depth, _, measurement = geometry_fixture()
    wrong_task = GeometryDetail(
        task_id=uuid4(),
        segmentation=segmentation,
        depth=depth,
        measurements=measurement,
    )
    with pytest.raises(ValueError, match="geometry.*task"):
        TaskReport(task=task, geometry=wrong_task)

    unrelated = task.model_dump(mode="json")
    unrelated["input_asset"] = {"asset_id": str(uuid4()), "version": 1}
    with pytest.raises(ValueError, match="geometry.*source"):
        TaskReport.model_validate(
            {
                "task": unrelated,
                "geometry": GeometryDetail(
                    task_id=task.snapshot.task_id,
                    segmentation=segmentation,
                    depth=depth,
                    measurements=measurement,
                ).model_dump(mode="json"),
            }
        )


def test_html_report_renders_real_geometry_assets_and_pixel_relative_semantics():
    task, segmentation, depth, _, measurement = geometry_fixture()
    detail = GeometryDetail(
        task_id=task.snapshot.task_id,
        segmentation=segmentation,
        depth=depth,
        measurements=measurement,
    )
    html = render_html(TaskReport(task=task, geometry=detail))

    for asset in (
        task.input_asset,
        segmentation.mask_asset.ref,
        segmentation.overlay_asset.ref,
        depth.preview_asset.ref,
        depth.raw_asset.ref,
    ):
        assert f"/api/assets/{asset.asset_id}/versions/{asset.version}/content" in html
    assert "6 像素" in html
    assert "12.5%" in html and "全图像素" in html
    assert "3 × 2 像素" in html
    assert "q10 3" in html and "中位数 4" in html and "q90 5" in html
    assert "相对模型单位" in html and "不能跨图比较" in html
    assert "白色=近，黑色=远" in html
    assert "掩膜质量估计" in html and "不是缺陷置信度" in html
    assert "毫米" not in html and "堵塞率" not in html
    assert "sam-pinned-v1" in html and "depth-pinned-v1" in html


async def test_json_and_html_report_load_stored_geometry_without_private_config(tmp_path):
    task, segmentation, depth, _, measurement = geometry_fixture()
    call = _call(task, segmentation, depth)

    def load_step(_, step):
        values = {
            "enhance": None,
            "enhancement_review": None,
            "enhancement_selection": None,
            "geometry_segment": {
                "result": segmentation.model_dump(mode="json"),
                "measurements": None,
            },
            "geometry_depth": {
                "result": depth.model_dump(mode="json"),
                "measurements": measurement.model_dump(mode="json"),
            },
        }
        return values.get(step)

    repo = SimpleNamespace(
        get_task=lambda _: task,
        list_calls=lambda _: (call,),
        load_step=load_step,
    )
    app = create_app(Settings(_env_file=None, asset_root=tmp_path), repository=repo)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        json_response = await client.get(f"/api/tasks/{task.snapshot.task_id}/report")
        html_response = await client.get(
            f"/api/tasks/{task.snapshot.task_id}/report?format=html"
        )

    assert json_response.status_code == 200
    assert json_response.json()["geometry"]["measurements"]["area_pixels"] == 6
    assert html_response.status_code == 200
    assert "白色=近，黑色=远" in html_response.text
    assert "checkpoint" not in json_response.text
    assert "/private" not in json_response.text


def test_old_report_payload_defaults_geometry_to_null():
    task, *_ = geometry_fixture()
    report = TaskReport.model_validate(
        {"schema_version": "1", "task": task.model_dump(mode="json")}
    )
    assert report.geometry is None
