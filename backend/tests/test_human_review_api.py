import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from pipe_inspector.agent.human_review import HumanReviewConflict, resolve_human_review
from pipe_inspector.api.app import create_app
from pipe_inspector.config import Settings
from pipe_inspector.contracts.assets import AssetRef
from pipe_inspector.contracts.findings import EvidenceRef, Finding
from pipe_inspector.contracts.human_review import HumanReviewRecord, HumanReviewSubmission
from pipe_inspector.contracts.tasks import TaskSnapshot
from pipe_inspector.storage.repository import MissingRecord

NOW = datetime(2026, 9, 11, 12, tzinfo=timezone.utc)


def review_case(*, version="human-review-v1", record=None):
    task_id = uuid4()
    finding = Finding(
        finding_id=uuid4(),
        task_id=task_id,
        canonical_label="AJ",
        review_status="needs_review",
        source="detector",
        score=0.8,
        evidence=(EvidenceRef(asset=AssetRef(asset_id=uuid4())),),
    )
    snapshot = TaskSnapshot(
        task_id=task_id,
        runtime_status="waiting_review",
        inspection_result="inconclusive",
        stage="human_review",
        stage_progress=1,
        overall_progress=0.95,
        waiting_reason="待人工确认",
        updated_at=NOW,
        findings=(finding,),
    )
    task = SimpleNamespace(snapshot=snapshot)
    repo = SimpleNamespace(
        get_task=lambda _: task,
        get_run=lambda _: {"config": {"human_review_version": version}} if version else None,
        get_human_review=lambda _: record,
    )
    body = HumanReviewSubmission(
        request_id=uuid4(),
        reviewer="huicod",
        snapshot_updated_at=NOW,
        decisions=(
            {
                "finding_id": finding.finding_id,
                "action": "reject",
                "reason": "人工判定不是缺陷",
            },
        ),
        negative_audit_complete=True,
    )
    return task_id, snapshot, repo, body


@pytest.mark.asyncio
async def test_review_get_reports_capability_and_post_returns_persisted_record(tmp_path):
    task_id, snapshot, repo, body = review_case()
    record = HumanReviewRecord(
        task_id=task_id,
        submission=body,
        before=snapshot,
        after=resolve_human_review(snapshot, body, now=NOW),
        reviewed_at=NOW,
    )
    starter = SimpleNamespace(submit=AsyncMock(return_value=record))
    app = create_app(
        Settings(_env_file=None, asset_root=tmp_path),
        repository=repo,
        human_review_starter=starter,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        detail = await client.get(f"/api/tasks/{task_id}/review")
        submitted = await client.post(
            f"/api/tasks/{task_id}/review", json=body.model_dump(mode="json")
        )

    assert detail.status_code == 200
    assert detail.json() == {
        "task_id": str(task_id),
        "can_review": True,
        "snapshot": snapshot.model_dump(mode="json"),
        "record": None,
    }
    assert submitted.status_code == 200
    assert submitted.json() == record.model_dump(mode="json")
    starter.submit.assert_awaited_once_with(task_id, body)


@pytest.mark.asyncio
async def test_review_get_preserves_history_but_does_not_reopen_terminal_or_legacy(tmp_path):
    for version in (None, "old-review-v0"):
        task_id, snapshot, repo, body = review_case(version=version)
        app = create_app(
            Settings(_env_file=None, asset_root=tmp_path),
            repository=repo,
            human_review_starter=AsyncMock(),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/api/tasks/{task_id}/review")
        assert response.status_code == 200
        assert response.json()["can_review"] is False

    terminal = resolve_human_review(snapshot, body, now=NOW)
    repo.get_task = lambda _: SimpleNamespace(snapshot=terminal)
    repo.get_run = lambda _: {"config": {"human_review_version": "human-review-v1"}}
    history = HumanReviewRecord(
        task_id=task_id,
        submission=body,
        before=snapshot,
        after=terminal,
        reviewed_at=NOW,
    )
    repo.get_human_review = lambda _: history
    app = create_app(
        Settings(_env_file=None, asset_root=tmp_path),
        repository=repo,
        human_review_starter=AsyncMock(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"/api/tasks/{task_id}/review")
    assert response.json()["can_review"] is False
    assert response.json()["record"] == history.model_dump(mode="json")


async def test_get_prefers_committed_audit_if_review_finishes_between_reads(tmp_path):
    task_id, snapshot, repo, body = review_case()
    record = HumanReviewRecord(
        task_id=task_id,
        submission=body,
        before=snapshot,
        after=resolve_human_review(snapshot, body, now=NOW),
        reviewed_at=NOW,
    )
    # get_task observed waiting, then the review committed before the audit read.
    repo.get_human_review = lambda _: record
    app = create_app(Settings(_env_file=None, asset_root=tmp_path), repository=repo)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"/api/tasks/{task_id}/review")
    assert response.status_code == 200
    assert response.json()["can_review"] is False
    assert response.json()["snapshot"] == record.after.model_dump(mode="json")


@pytest.mark.asyncio
async def test_review_http_maps_missing_and_domain_conflicts_safely(tmp_path):
    task_id, _, repo, body = review_case()
    repo.get_task = lambda _: (_ for _ in ()).throw(MissingRecord())
    starter = SimpleNamespace(
        submit=AsyncMock(
            side_effect=HumanReviewConflict("review_conflict", "复核内容与当前任务不一致")
        )
    )
    app = create_app(
        Settings(_env_file=None, asset_root=tmp_path),
        repository=repo,
        human_review_starter=starter,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        missing = await client.get(f"/api/tasks/{task_id}/review")
        conflict = await client.post(
            f"/api/tasks/{task_id}/review", json=body.model_dump(mode="json")
        )
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "not_found"
    assert conflict.status_code == 409
    assert conflict.json() == {
        "detail": {
            "code": "review_conflict",
            "message": "复核内容与当前任务不一致",
        }
    }


@pytest.mark.parametrize("chunked", [False, True])
async def test_complete_review_larger_than_old_json_limit_reaches_starter(tmp_path, chunked):
    task_id, snapshot, repo, original = review_case()
    findings = tuple(
        Finding.model_validate(
            snapshot.findings[0].model_dump()
            | {
                "finding_id": uuid4(),
            }
        )
        for _ in range(70)
    )
    snapshot = TaskSnapshot.model_validate(snapshot.model_dump() | {"findings": findings})
    body = HumanReviewSubmission.model_validate(
        original.model_dump()
        | {
            "decisions": tuple(
                {"finding_id": f.finding_id, "action": "reject", "reason": "核" * 1000}
                for f in findings
            ),
        }
    )
    record = HumanReviewRecord(
        task_id=task_id,
        submission=body,
        before=snapshot,
        after=resolve_human_review(snapshot, body, now=NOW),
        reviewed_at=NOW,
    )
    starter = SimpleNamespace(submit=AsyncMock(return_value=record))
    app = create_app(
        Settings(_env_file=None, asset_root=tmp_path), repository=repo, human_review_starter=starter
    )
    raw = json.dumps(body.model_dump(mode="json"), ensure_ascii=False).encode()
    assert 65_536 < len(raw) < 1_000_000

    async def chunks():
        for offset in range(0, len(raw), 30_000):
            yield raw[offset : offset + 30_000]

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.post(
            f"/api/tasks/{task_id}/review",
            content=chunks() if chunked else raw,
            headers={"content-type": "application/json"},
        )
    assert result.status_code == 200
    starter.submit.assert_awaited_once_with(task_id, body)


@pytest.mark.parametrize("chunked", [False, True])
async def test_oversized_review_transport_still_stops_before_starter(tmp_path, chunked):
    task_id, _, repo, _ = review_case()
    starter = SimpleNamespace(submit=AsyncMock())
    app = create_app(
        Settings(_env_file=None, asset_root=tmp_path), repository=repo, human_review_starter=starter
    )

    async def chunks():
        yield b" " * 1_100_000
        yield b" " * 1_100_000

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.post(
            f"/api/tasks/{task_id}/review",
            content=chunks() if chunked else b" " * 2_200_000,
            headers={"content-type": "application/json"},
        )
    assert result.status_code == 413
    starter.submit.assert_not_awaited()
