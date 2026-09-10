from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pipe_inspector.api.app import create_app
from pipe_inspector.config import Settings
from pipe_inspector.contracts.assets import AssetRef
from pipe_inspector.contracts.findings import EvidenceRef, Finding
from pipe_inspector.contracts.intake import TaskRecord
from pipe_inspector.contracts.reports import TaskReport
from pipe_inspector.contracts.tasks import TaskSnapshot
from pipe_inspector.contracts.tools import ToolCall, ToolError
from pipe_inspector.reports import render_html
from pipe_inspector.storage.repository import MissingRecord


def report_fixture(status="partial"):
    task_id, asset = uuid4(), AssetRef(asset_id=uuid4())
    now = datetime.now(timezone.utc)
    finding = Finding(
        finding_id=uuid4(),
        task_id=task_id,
        canonical_label="OTHER",
        open_label='<script>alert("candidate")</script>',
        review_status="needs_review",
        source="vlm",
        evidence=(EvidenceRef(asset=asset),),
    )
    snapshot = TaskSnapshot(
        task_id=task_id,
        updated_at=now,
        runtime_status=status,
        inspection_result="inconclusive" if status == "partial" else "pending",
        findings=(finding,),
        waiting_reason="需要人工复核",
    )
    task = TaskRecord(
        request_id=uuid4(),
        session_id=uuid4(),
        input_asset=asset,
        instruction='<img src=x onerror="alert(1)">请检查',
        created_at=now,
        snapshot=snapshot,
    )
    call = ToolCall(
        call_id=uuid4(),
        task_id=task_id,
        step_id="verify",
        idempotency_key="verify-once",
        tool_name="verify",
        tool_version="1",
        model_version="fixture-qwen",
        resource="api",
        timeout_seconds=60,
        input_assets=(asset,),
        status="uncertain",
        error=ToolError(code="timeout", retryable=True, message="工具执行未完成：timeout"),
    )
    return task, call


def test_supplement_report_links_original_without_inventing_a_detection_call():
    task, call = report_fixture()
    parent_id = uuid4()
    child = TaskRecord.model_validate(task.model_dump() | {"parent_task_id": parent_id})
    html = render_html(TaskReport(task=child, tool_calls=(call,)))
    assert f"/api/tasks/{parent_id}/report?format=html" in html
    assert "原任务报告" in html and "未重新运行检测器" in html
    assert "detect v1" not in html


@pytest.fixture
def report_app(tmp_path):
    task, call = report_fixture()
    reads = []

    def get_task(task_id):
        reads.append("task")
        if task_id != task.snapshot.task_id:
            raise MissingRecord()
        return task

    def list_calls(task_id):
        reads.append("calls")
        return (call,)

    # No create/start/model methods: report generation must be strictly read-only.
    repo = SimpleNamespace(get_task=get_task, list_calls=list_calls)
    app = create_app(Settings(_env_file=None, asset_root=tmp_path), repository=repo)
    return app, task, call, reads


async def test_json_report_preserves_stored_evidence_and_unresolved_result(report_app):
    app, task, call, reads = report_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/tasks/{task.snapshot.task_id}/report?format=json")
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "1"
    assert payload["task"] == task.model_dump(mode="json")
    assert payload["tool_calls"] == [call.model_dump(mode="json")]
    assert reads == ["task", "calls"]


async def test_html_report_escapes_text_and_keeps_review_coverage_and_tool_failure(report_app):
    app, task, _, _ = report_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/tasks/{task.snapshot.task_id}/report?format=html")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<script>" not in response.text
    assert "<img src=x" not in response.text
    assert "&lt;script&gt;" in response.text
    assert "无法判定" in response.text
    assert "待人工复核" in response.text
    assert "覆盖不足" in response.text
    assert "timeout" in response.text and "uncertain" in response.text
    assert "fixture-qwen" in response.text
    assert f"/api/assets/{task.input_asset.asset_id}/versions/1/content" in response.text
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_report_rejects_bad_format_and_unknown_task(report_app):
    app, task, _, reads = report_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        bad = await client.get(f"/api/tasks/{task.snapshot.task_id}/report?format=pdf")
        assert bad.status_code == 422 and not reads
        missing = await client.get(f"/api/tasks/{uuid4()}/report")
    assert missing.status_code == 404


async def test_report_does_not_present_running_snapshot_as_final(tmp_path):
    task, _ = report_fixture("running")
    repo = SimpleNamespace(get_task=lambda _: task)
    app = create_app(Settings(_env_file=None, asset_root=tmp_path), repository=repo)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/tasks/{task.snapshot.task_id}/report")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "report_not_ready"


@pytest.mark.parametrize("result", ["normal", "inconclusive"])
async def test_report_preserves_valid_normal_or_failed_conclusion(tmp_path, result):
    task, _ = report_fixture()
    snapshot = TaskSnapshot(
        task_id=task.snapshot.task_id,
        updated_at=task.snapshot.updated_at,
        runtime_status="completed" if result == "normal" else "failed",
        inspection_result=result,
        coverage={
            "sufficient_quality": result == "normal",
            "sufficient_coverage": result == "normal",
            "negative_audit_complete": result == "normal",
        },
    )
    record = TaskRecord.model_validate(task.model_dump() | {"snapshot": snapshot})
    repo = SimpleNamespace(get_task=lambda _: record, list_calls=lambda _: ())
    app = create_app(Settings(_env_file=None, asset_root=tmp_path), repository=repo)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/tasks/{snapshot.task_id}/report")
    assert response.status_code == 200
    assert response.json()["task"]["snapshot"] == snapshot.model_dump(mode="json")


def test_report_contract_rejects_calls_from_another_task():
    task, _ = report_fixture()
    _, unrelated_call = report_fixture()
    with pytest.raises(ValueError, match="must belong"):
        TaskReport(task=task, tool_calls=(unrelated_call,))
