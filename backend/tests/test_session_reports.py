from datetime import datetime, timezone
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
from pipe_inspector.api.app import create_app
from pipe_inspector.config import Settings
from pipe_inspector.contracts.assets import AssetRef
from pipe_inspector.contracts.findings import EvidenceRef, Finding
from pipe_inspector.contracts.intake import SessionDetail, SessionRecord, TaskRecord
from pipe_inspector.contracts.reports import (
    SessionReport,
    SessionReportAssetGroup,
    SessionReportSource,
)
from pipe_inspector.contracts.tasks import TaskSnapshot
from pipe_inspector.contracts.turns import TurnRecord
from pipe_inspector.session_report import render_session_html
from pipe_inspector.storage.repository import MissingRecord
from test_geometry_api import geometry_fixture


def _task(*, session_id, asset, status, parent_task_id=None, instruction="检查 <管道>"):
    now = datetime.now(timezone.utc)
    task_id = uuid4()
    finding = Finding(
        finding_id=uuid4(),
        task_id=task_id,
        canonical_label="PL",
        review_status="needs_review",
        source="vlm",
        evidence=(EvidenceRef(asset=asset),),
    )
    snapshot = TaskSnapshot(
        task_id=task_id,
        runtime_status=status,
        stage="verifying" if status == "running" else "finished",
        stage_progress=0.5 if status == "running" else 1,
        overall_progress=0.4 if status == "running" else 1,
        inspection_result="pending" if status == "running" else "inconclusive",
        findings=(finding,),
        updated_at=now,
    )
    return TaskRecord(
        request_id=uuid4(),
        session_id=session_id,
        input_asset=asset,
        instruction=instruction,
        created_at=now,
        snapshot=snapshot,
        parent_task_id=parent_task_id,
    )


def _app(tmp_path):
    session = SessionRecord(session_id=uuid4(), created_at=datetime.now(timezone.utc))
    asset = AssetRef(asset_id=uuid4())
    root = _task(session_id=session.session_id, asset=asset, status="running")
    second_root = _task(session_id=session.session_id, asset=asset, status="partial")
    child = _task(
        session_id=session.session_id,
        asset=asset,
        status="partial",
        parent_task_id=root.snapshot.task_id,
    )
    turn = TurnRecord(
        request_id=uuid4(),
        session_id=session.session_id,
        text="继续检查",
        input_asset=asset,
        task_id=root.snapshot.task_id,
        linked_task_id=child.snapshot.task_id,
        created_at=session.created_at,
        updated_at=session.created_at,
        status="running",
        stage="executing",
    )
    calls = []

    class Repo:
        def get_session(self, session_id):
            if session_id != session.session_id:
                raise MissingRecord
            return SessionDetail(session=session, tasks=(root, second_root, child))

        def list_turns(self, session_id, limit=50):
            assert session_id == session.session_id
            assert limit is None
            return (turn,)

        def list_calls(self, task_id):
            calls.append(task_id)
            return ()

        def load_step(self, task_id, step):
            return None

    app = create_app(Settings(_env_file=None, asset_root=tmp_path), repository=Repo())
    return app, session, root, second_root, child, turn, calls


async def test_session_json_groups_same_asset_sources_and_exposes_running_progress(tmp_path):
    app, session, root, second_root, child, turn, calls = _app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/sessions/{session.session_id}/report")

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "1"
    assert payload["is_final"] is False
    assert payload["turns"] == [turn.model_dump(mode="json")]
    assert len(payload["asset_groups"]) == 1
    sources = payload["asset_groups"][0]["sources"]
    assert [source["task_id"] for source in sources] == [
        str(root.snapshot.task_id),
        str(second_root.snapshot.task_id),
        str(child.snapshot.task_id),
    ]
    assert [source["root_task_id"] for source in sources] == [
        str(root.snapshot.task_id),
        str(second_root.snapshot.task_id),
        str(root.snapshot.task_id),
    ]
    assert [source["role"] for source in sources] == ["primary", "primary", "supplement"]
    assert sources[0]["task"]["snapshot"]["overall_progress"] == 0.4
    assert "finding_count" not in payload and "defect_count" not in payload
    assert calls == [root.snapshot.task_id, second_root.snapshot.task_id, child.snapshot.task_id]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_session_html_escapes_text_and_marks_sources_as_non_additive(tmp_path):
    app, session, root, second_root, child, _, _ = _app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/sessions/{session.session_id}/report?format=html")

    assert response.status_code == 200
    assert "检查 &lt;管道&gt;" in response.text
    assert "检查 <管道>" not in response.text
    assert "当前为非最终快照" in response.text
    assert "不跨任务累计为独立缺陷总数" in response.text
    for task in (root, second_root, child):
        assert str(task.snapshot.task_id) in response.text
    assert response.headers["content-security-policy"].startswith("default-src 'none'")
    assert "img-src 'self'" in response.headers["content-security-policy"]


async def test_session_report_rejects_bad_format_before_storage_and_unknown_session(tmp_path):
    app, session, *_ = _app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        bad = await client.get(f"/api/sessions/{session.session_id}/report?format=pdf")
        missing = await client.get(f"/api/sessions/{uuid4()}/report")
    assert bad.status_code == 422
    assert missing.status_code == 404


def test_session_report_contract_rejects_cross_session_source():
    session = SessionRecord(session_id=uuid4(), created_at=datetime.now(timezone.utc))
    task = _task(
        session_id=uuid4(),
        asset=AssetRef(asset_id=uuid4()),
        status="partial",
    )
    source = SessionReportSource(
        task_id=task.snapshot.task_id,
        root_task_id=task.snapshot.task_id,
        role="primary",
        task=task,
    )
    try:
        SessionReport(
            session=session,
            generated_at=session.created_at,
            is_final=True,
            asset_groups=(
                SessionReportAssetGroup(input_asset=task.input_asset, sources=(source,)),
            ),
        )
    except ValueError as error:
        assert "must belong to the session" in str(error)
    else:
        raise AssertionError("cross-session source was accepted")


def test_session_html_includes_turn_reply_and_geometry_evidence_details():
    task, segmentation, depth, _, measurements = geometry_fixture()
    session = SessionRecord(session_id=task.session_id, created_at=task.created_at)
    turn = TurnRecord(
        request_id=uuid4(),
        session_id=task.session_id,
        text="请量测",
        reply="已完成 <量测>",
        linked_task_id=task.snapshot.task_id,
        created_at=task.created_at,
        updated_at=task.created_at,
        status="completed",
        stage="finished",
    )
    source = SessionReportSource(
        task_id=task.snapshot.task_id,
        root_task_id=task.snapshot.task_id,
        role="primary",
        task=task,
        geometry={
            "task_id": task.snapshot.task_id,
            "segmentation": segmentation,
            "depth": depth,
            "measurements": measurements,
        },
    )
    report = SessionReport(
        session=session,
        generated_at=task.created_at,
        is_final=True,
        turns=(turn,),
        asset_groups=(
            SessionReportAssetGroup(input_asset=task.input_asset, sources=(source,)),
        ),
    )

    html = render_session_html(report)

    assert "已完成 &lt;量测&gt;" in html
    assert f"/api/tasks/{task.snapshot.task_id}/report?format=html" in html
    for asset in (
        segmentation.mask_asset.ref,
        segmentation.overlay_asset.ref,
        depth.preview_asset.ref,
        depth.raw_asset.ref,
    ):
        assert f"/api/assets/{asset.asset_id}/versions/{asset.version}/content" in html
    assert "6 像素" in html and "12.5%" in html
    assert "白色=近，黑色=远" in html
