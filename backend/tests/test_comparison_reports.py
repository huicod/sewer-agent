from html import unescape
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from pipe_inspector.api.app import create_app
from pipe_inspector.comparison_report import render_comparison_html
from pipe_inspector.config import Settings
from test_comparison_api import comparison_fixture


def test_html_report_escapes_text_and_shows_sources_evidence_counts_and_uncertainty():
    _, result = comparison_fixture()

    html = render_comparison_html(result)

    assert "检查 &lt;当前&gt; &amp; 保留原证据" in html
    assert "覆盖不足 &lt;需补拍&gt;" in html
    assert "<当前>" not in html and "<需补拍>" not in html
    for task in (result.reference, result.current):
        assert str(task.snapshot.task_id) in html
        assert task.created_at.isoformat() in html
        assert task.snapshot.updated_at.isoformat() in html
        assert f"/api/assets/{task.input_asset.asset_id}/versions/1/content" in html
        for finding in task.snapshot.findings:
            assert str(finding.finding_id) in html
    assert "帧 2；时间 1 秒" in html
    assert html.split("<tbody>", 1)[1].count("<tr><th>") == 7
    assert "AJ · 支管暗接" in html
    assert "OTHER · 其他类别" in html
    assert "<td>1</td><td>0</td><td>0</td><td>0</td>" in html
    assert "<td>2</td><td>0</td><td>0</td><td>1</td><td>+1</td>" in html
    assert "发现匹配未经验证" in html
    assert "不能证明缺陷已修复或出现新缺陷" in html
    assert "维修状态" in html
    assert "准确率" not in unescape(html)


async def test_comparison_reports_export_exact_snapshot_and_409_when_absent(tmp_path):
    turn, result = comparison_fixture()
    stored = result.model_dump(mode="json")
    repo = SimpleNamespace(
        get_turn=lambda _: turn,
        load_turn_step=lambda *_: stored,
    )
    app = create_app(Settings(_env_file=None, asset_root=tmp_path), repository=repo)
    base = f"/api/sessions/{turn.session_id}/turns/{turn.request_id}/comparison/report"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        json_response = await client.get(base)
        html_response = await client.get(f"{base}?format=html")
        repo.load_turn_step = lambda *_: None
        missing = await client.get(base)

    assert json_response.status_code == 200
    assert json_response.json() == stored
    assert json_response.headers["cache-control"] == "no-store"
    assert json_response.headers["x-content-type-options"] == "nosniff"
    assert html_response.status_code == 200
    assert html_response.headers["content-security-policy"].startswith("default-src 'none'")
    assert "发现匹配未经验证" in html_response.text
    assert missing.status_code == 409
    assert missing.json() == {
        "detail": {
            "code": "comparison_not_ready",
            "message": "该对话尚无已保存的对比结果",
        }
    }


async def test_report_rejects_invalid_format_without_reading_snapshot(tmp_path):
    turn, _ = comparison_fixture()
    repo = SimpleNamespace(
        get_turn=lambda _: pytest.fail("invalid query must fail before handler"),
        load_turn_step=lambda *_: pytest.fail("invalid query must not read storage"),
    )
    app = create_app(Settings(_env_file=None, asset_root=tmp_path), repository=repo)
    path = f"/api/sessions/{turn.session_id}/turns/{turn.request_id}/comparison/report?format=pdf"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(path)

    assert response.status_code == 422
