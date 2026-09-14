from uuid import uuid4

from pipe_inspector.contracts.assets import AssetRef
from pipe_inspector.contracts.intake import TaskRecord
from pipe_inspector.contracts.reports import TaskReport
from pipe_inspector.reports import render_html
from test_reports import report_fixture


def test_frame_report_has_source_and_single_frame_scope_without_findings():
    task, _ = report_fixture()
    parent_id = uuid4()
    source_video = AssetRef(asset_id=uuid4())
    task = TaskRecord.model_validate(task.model_dump() | {
        "parent_task_id": parent_id,
        "frame_source": {
            "video_asset": source_video,
            "frame_index": 42,
            "timestamp_seconds": 1.68,
        },
        "snapshot": task.snapshot.model_dump() | {"findings": ()},
    })
    report = TaskReport(task=task)
    html = render_html(report)
    assert "仅针对所选视频帧" in html
    assert "帧 42" in html and "1.68 秒" in html
    assert "整段视频结论不变" in html
    assert f"/api/tasks/{parent_id}/report?format=html" in html
    assert f"/api/assets/{source_video.asset_id}/versions/1/content" in html
    assert "复用原图片" not in html
    assert report.model_dump(mode="json")["task"]["frame_source"]["frame_index"] == 42
