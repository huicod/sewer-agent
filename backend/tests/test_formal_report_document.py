from datetime import datetime, timezone
from uuid import uuid4

from pipe_inspector.contracts.assets import AssetRef
from pipe_inspector.contracts.intake import SessionRecord, TaskRecord
from pipe_inspector.contracts.reports import (
    SessionReport,
    SessionReportAssetGroup,
    SessionReportSource,
    TaskReport,
)
from pipe_inspector.reports import render_html
from pipe_inspector.session_report import render_session_html
from test_reports import report_fixture


def test_formal_task_document_has_tables_and_proven_image_evidence():
    task, call = report_fixture()
    finding = task.snapshot.findings[0].model_dump() | {
        "score": 0.81,
        "evidence": [{
            "asset": task.input_asset,
            "region": {"width": 640, "height": 480, "bbox_xyxy": [10, 20, 100, 200]},
        }],
    }
    task = TaskRecord.model_validate(task.model_dump() | {
        "snapshot": task.snapshot.model_dump() | {"findings": [finding]},
    })

    html = render_html(TaskReport(task=task, tool_calls=(call,)))

    assert '<main class="report-document">' in html
    assert '<table class="metadata-table">' in html
    assert '<table class="findings-table">' in html
    assert '<table class="tool-table">' in html
    assert "@media print" in html
    assert f'<img src="/api/assets/{task.input_asset.asset_id}/versions/1/content"' in html
    assert "原检测候选分数：0.8100" in html
    assert "不是融合类别置信度" in html
    assert str(task.snapshot.findings[0].finding_id) in html
    assert "&lt;script&gt;" in html and "<script>" not in html
    assert "&lt;img src=x" in html and "<img src=x" not in html


def test_unknown_media_keeps_source_link_without_guessing_an_image():
    task, _ = report_fixture()
    html = render_html(TaskReport(task=task))
    path = f"/api/assets/{task.input_asset.asset_id}/versions/1/content"
    assert f'href="{path}"' in html
    assert f'<img src="{path}"' not in html


def test_video_evidence_previews_exact_saved_frame_not_original_video():
    task, _ = report_fixture()
    frame = AssetRef(asset_id=uuid4(), version=2)
    finding = task.snapshot.findings[0].model_dump() | {
        "evidence": [{"asset": frame, "frame_index": 19, "timestamp_seconds": 0.76}],
    }
    task = TaskRecord.model_validate(task.model_dump() | {
        "snapshot": task.snapshot.model_dump() | {"findings": [finding]},
    })
    html = render_html(TaskReport(task=task))
    assert f'<img src="/api/assets/{frame.asset_id}/versions/2/content"' in html
    assert f'<img src="/api/assets/{task.input_asset.asset_id}/versions/1/content"' not in html
    assert "帧 19" in html and "0.76 秒" in html


def test_legacy_video_locator_keeps_original_link_but_previews_only_saved_frame():
    task, _ = report_fixture()
    frame = AssetRef(asset_id=uuid4(), version=2)
    locator = {
        "frame_index": 19,
        "timestamp_seconds": 0.76,
        "region": {"width": 640, "height": 480, "bbox_xyxy": [10, 20, 100, 200]},
    }
    finding = task.snapshot.findings[0].model_dump() | {
        "evidence": [
            {"asset": task.input_asset, **locator},
            {"asset": frame, **locator},
        ],
    }
    task = TaskRecord.model_validate(task.model_dump() | {
        "snapshot": task.snapshot.model_dump() | {"findings": [finding]},
    })
    html = render_html(TaskReport(task=task))
    source_path = f"/api/assets/{task.input_asset.asset_id}/versions/1/content"
    assert f'href="{source_path}"' in html
    assert f'<img src="{source_path}"' not in html
    assert f'<img src="/api/assets/{frame.asset_id}/versions/2/content"' in html
    assert "帧 19" in html and "0.76 秒" in html


def test_frame_tool_input_with_video_locator_remains_a_previewable_saved_frame():
    task, _ = report_fixture()
    finding = task.snapshot.findings[0].model_dump() | {
        "evidence": [{"asset": task.input_asset, "frame_index": 19, "timestamp_seconds": 0.76}],
    }
    task = TaskRecord.model_validate(task.model_dump() | {
        "parent_task_id": uuid4(),
        "frame_source": {"video_asset": AssetRef(asset_id=uuid4()),
                         "frame_index": 19, "timestamp_seconds": 0.76},
        "snapshot": task.snapshot.model_dump() | {"findings": [finding]},
    })
    html = render_html(TaskReport(task=task))
    assert f'<img src="/api/assets/{task.input_asset.asset_id}/versions/1/content"' in html
    assert "仅针对所选视频帧" in html


def test_session_document_keeps_separate_source_tables_and_non_final_scope():
    task, _ = report_fixture("running")
    session = SessionRecord(
        session_id=task.session_id,
        created_at=datetime.now(timezone.utc),
    )
    source = SessionReportSource(
        task_id=task.snapshot.task_id,
        root_task_id=task.snapshot.task_id,
        role="primary",
        task=task,
    )
    report = SessionReport(
        session=session,
        generated_at=session.created_at,
        is_final=False,
        asset_groups=(SessionReportAssetGroup(input_asset=task.input_asset, sources=(source,)),),
    )
    html = render_session_html(report)
    assert '<table class="source-table">' in html
    assert '<table class="findings-table">' in html
    assert "当前为非最终快照" in html
    assert "不跨任务累计为独立缺陷总数" in html
    assert "&lt;script&gt;alert" in html
    assert "<script>" not in html
    assert f'href="/api/tasks/{task.snapshot.task_id}/report?format=html"' not in html
