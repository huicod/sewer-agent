from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pipe_inspector.api.app import create_app
from pipe_inspector.api.conversation import load_comparison_result
from pipe_inspector.comparison import build_comparison
from pipe_inspector.config import Settings
from pipe_inspector.contracts.assets import AssetRecord, AssetRef
from pipe_inspector.contracts.findings import EvidenceRef, Finding
from pipe_inspector.contracts.intake import TaskRecord
from pipe_inspector.contracts.tasks import TaskSnapshot
from pipe_inspector.contracts.turns import TurnRecord
from pipe_inspector.storage.repository import MissingRecord

NOW = datetime(2026, 9, 13, 12, tzinfo=timezone.utc)


def _media() -> AssetRecord:
    return AssetRecord(
        ref=AssetRef(asset_id=uuid4()),
        kind="video",
        media_type="video/mp4",
        byte_size=512,
        width=16,
        height=12,
        duration_seconds=5,
    )


def _task(session_id, media, findings_spec, *, waiting_reason=None) -> TaskRecord:
    task_id = uuid4()
    findings = tuple(
        Finding(
            finding_id=uuid4(),
            task_id=task_id,
            canonical_label=label,
            open_label="待分类异常" if label is None else None,
            review_status=status,
            source="human" if label == "OTHER" else "detector",
            evidence=(
                EvidenceRef(
                    asset=media.ref,
                    frame_index=index + 2,
                    timestamp_seconds=(index + 2) / 2,
                ),
            ),
            confirmed_by=("human" if label == "OTHER" else "rules")
            if status == "confirmed"
            else None,
        )
        for index, (label, status) in enumerate(findings_spec)
    )
    return TaskRecord(
        request_id=uuid4(),
        session_id=session_id,
        input_asset=media.ref,
        instruction="检查 <当前> & 保留原证据",
        created_at=NOW,
        snapshot=TaskSnapshot(
            task_id=task_id,
            runtime_status="partial" if waiting_reason else "completed",
            inspection_result="defects_found",
            stage="finished",
            stage_progress=1,
            overall_progress=1,
            updated_at=NOW,
            findings=findings,
            waiting_reason=waiting_reason,
        ),
    )


def comparison_fixture():
    session_id = uuid4()
    request_id = uuid4()
    reference_media = _media()
    current_media = _media()
    reference = _task(session_id, reference_media, (("AJ", "confirmed"),))
    current = _task(
        session_id,
        current_media,
        (
            ("AJ", "confirmed"),
            ("AJ", "confirmed"),
            ("AJ", "rejected"),
            (None, "needs_review"),
            ("OTHER", "confirmed"),
        ),
        waiting_reason="覆盖不足 <需补拍>",
    )
    result = build_comparison(
        request_id,
        reference,
        current,
        reference_media,
        current_media,
        attempt=3,
        created_at=NOW,
    )
    turn = TurnRecord(
        request_id=request_id,
        session_id=session_id,
        text="对比保存结果",
        task_id=current.snapshot.task_id,
        reference_task_id=reference.snapshot.task_id,
        created_at=NOW,
        updated_at=NOW,
        status="completed",
        stage="finished",
        reply="已完成保存结果对比",
        linked_task_id=current.snapshot.task_id,
        attempts=3,
    )
    return turn, result


def test_loader_returns_cached_snapshot_and_null_without_source_rereads():
    turn, result = comparison_fixture()
    reads = []
    repo = SimpleNamespace(
        get_turn=lambda request_id: (reads.append(("turn", request_id)) or turn),
        load_turn_step=lambda request_id, step: (
            reads.append((step, request_id)) or result.model_dump(mode="json")
        ),
    )

    loaded = load_comparison_result(repo, turn.session_id, turn.request_id)

    assert loaded == result
    assert reads == [("turn", turn.request_id), ("comparison", turn.request_id)]
    repo.load_turn_step = lambda *_: None
    assert load_comparison_result(repo, turn.session_id, turn.request_id) is None

    text_reference = TurnRecord.model_validate(
        turn.model_dump(mode="json") | {"reference_task_id": None, "task_id": None}
    )
    repo.get_turn = lambda _: text_reference
    repo.load_turn_step = lambda *_: result.model_dump(mode="json")
    assert load_comparison_result(repo, turn.session_id, turn.request_id) == result

    recovery_turn = TurnRecord.model_validate(
        turn.model_dump(mode="json")
        | {
            "status": "running",
            "stage": "executing",
            "reply": None,
            "task_id": None,
            "reference_task_id": None,
            "linked_task_id": None,
        }
    )
    repo.get_turn = lambda _: recovery_turn
    assert load_comparison_result(repo, turn.session_id, turn.request_id) == result


@pytest.mark.parametrize(
    "mutation",
    ["request", "reference", "current_body", "current_link", "session", "upload"],
)
def test_loader_rejects_mismatched_turn_and_snapshot_bindings(mutation):
    turn, result = comparison_fixture()
    turn_payload = turn.model_dump(mode="json")
    result_payload = result.model_dump(mode="json")
    if mutation == "request":
        result_payload["request_id"] = str(uuid4())
    elif mutation == "reference":
        turn_payload["reference_task_id"] = str(uuid4())
    elif mutation == "current_body":
        turn_payload["task_id"] = str(uuid4())
    elif mutation == "current_link":
        turn_payload["linked_task_id"] = str(uuid4())
    elif mutation == "session":
        result_payload["reference"]["session_id"] = str(uuid4())
        result_payload["current"]["session_id"] = result_payload["reference"]["session_id"]
    else:
        turn_payload["input_asset"] = result_payload["current"]["input_asset"]
    bound_turn = TurnRecord.model_validate(turn_payload)
    repo = SimpleNamespace(
        get_turn=lambda _: bound_turn,
        load_turn_step=lambda *_: result_payload,
    )

    with pytest.raises(ValueError, match="requested turn and sources"):
        load_comparison_result(repo, turn.session_id, turn.request_id)


def test_loader_hides_foreign_session_before_reading_snapshot():
    turn, _ = comparison_fixture()
    repo = SimpleNamespace(
        get_turn=lambda _: turn,
        load_turn_step=lambda *_: pytest.fail("must not read a foreign turn snapshot"),
    )
    with pytest.raises(MissingRecord):
        load_comparison_result(repo, uuid4(), turn.request_id)


async def test_comparison_endpoint_returns_validated_snapshot_or_null(tmp_path):
    turn, result = comparison_fixture()
    stored = result.model_dump(mode="json")
    repo = SimpleNamespace(
        get_turn=lambda _: turn,
        load_turn_step=lambda *_: stored,
    )
    app = create_app(Settings(_env_file=None, asset_root=tmp_path), repository=repo)
    path = f"/api/sessions/{turn.session_id}/turns/{turn.request_id}/comparison"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(path)
        repo.load_turn_step = lambda *_: None
        empty = await client.get(path)
        foreign = await client.get(path.replace(str(turn.session_id), str(uuid4())))

    assert response.status_code == 200
    assert response.json() == stored
    assert empty.status_code == 200 and empty.json() is None
    assert foreign.status_code == 404
