from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pipe_inspector.agent.human_review import HumanReviewConflict, resolve_human_review
from pipe_inspector.config import Settings
from pipe_inspector.contracts.assets import AssetRef
from pipe_inspector.contracts.findings import EvidenceRef, Finding
from pipe_inspector.contracts.human_review import (
    FindingReview,
    HumanReviewRecord,
    HumanReviewSubmission,
)
from pipe_inspector.contracts.tasks import CoverageSummary, TaskSnapshot
from pipe_inspector.storage.repository import SubmissionConflict
from pipe_inspector.workflow.human_review_start import HumanReviewStarter
from pipe_inspector.workflow.start import InspectionStartError
from pydantic import ValidationError
from temporalio.client import WorkflowUpdateFailedError
from temporalio.exceptions import ApplicationError

NOW = datetime(2026, 9, 11, 12, tzinfo=timezone.utc)


def finding(task_id, *, label="AJ", status="needs_review", source="detector", score=0.7):
    values = {
        "finding_id": uuid4(),
        "task_id": task_id,
        "canonical_label": label,
        "review_status": status,
        "source": source,
        "score": score,
        "evidence": (EvidenceRef(asset=AssetRef(asset_id=uuid4())),),
    }
    if status == "confirmed":
        values["confirmed_by"] = "rules"
    if label == "OTHER":
        values["open_label"] = "模型描述"
    return Finding(**values)


def waiting_snapshot(*, coverage=None, findings=None, reason="自动检查待人工确认"):
    task_id = findings[0].task_id if findings else uuid4()
    return TaskSnapshot(
        task_id=task_id,
        runtime_status="waiting_review",
        inspection_result="inconclusive",
        stage="human_review",
        stage_progress=1,
        overall_progress=0.95,
        can_cancel=True,
        waiting_reason=reason,
        updated_at=NOW,
        findings=tuple(findings or (finding(task_id),)),
        coverage=coverage or CoverageSummary(),
    )


def submission(snapshot, *decisions, audit=False, note="已核对原图"):
    return HumanReviewSubmission(
        request_id=uuid4(),
        reviewer="huicod",
        snapshot_updated_at=snapshot.updated_at,
        decisions=decisions,
        negative_audit_complete=audit,
        note=note,
    )


def review(finding, action, **changes):
    return FindingReview(
        finding_id=finding.finding_id,
        action=action,
        reason="人工复核决定",
        **changes,
    )


def test_review_contract_rejects_replacement_labels_and_invalid_other_names():
    finding_id = uuid4()
    with pytest.raises(ValidationError):
        FindingReview(
            finding_id=finding_id,
            action="confirm",
            label="FS",
            reason="不能在确认时换类",
        )
    with pytest.raises(ValidationError):
        FindingReview(
            finding_id=finding_id,
            action="reject",
            open_label="新类别",
            reason="拒绝不应带类别",
        )
    with pytest.raises(ValidationError):
        FindingReview(
            finding_id=finding_id,
            action="relabel",
            label="OTHER",
            reason="OTHER 必须由人工命名",
        )
    assert (
        FindingReview(
            finding_id=finding_id,
            action="confirm",
            open_label="树根侵入",
            reason="确认已有 OTHER",
        ).open_label
        == "树根侵入"
    )


def test_submission_rejects_duplicate_decisions_and_naive_snapshot_time():
    decision = FindingReview(finding_id=uuid4(), action="reject", reason="不是缺陷")
    with pytest.raises(ValidationError):
        HumanReviewSubmission(
            request_id=uuid4(),
            reviewer="huicod",
            snapshot_updated_at=NOW.replace(tzinfo=None),
            decisions=(decision,),
        )
    with pytest.raises(ValidationError, match="duplicate finding"):
        HumanReviewSubmission(
            request_id=uuid4(),
            reviewer="huicod",
            snapshot_updated_at=NOW,
            decisions=(decision, decision),
        )


def test_submission_rejects_encoded_payload_larger_than_temporal_history_bound():
    decisions = tuple(
        FindingReview(finding_id=uuid4(), action="reject", reason="汉" * 1000) for _ in range(400)
    )
    with pytest.raises(ValidationError, match="encoded submission exceeds"):
        HumanReviewSubmission(
            request_id=uuid4(),
            reviewer="huicod",
            snapshot_updated_at=NOW,
            decisions=decisions,
        )


def test_temporal_json_escape_size_is_also_bounded():
    # Non-BMP characters occupy 4 UTF-8 bytes but 12 JSON escape bytes in Temporal's
    # default JSON converter. Counting unescaped text alone misses this case.
    decisions = tuple(
        FindingReview(finding_id=uuid4(), action="reject", reason="🔍" * 1000) for _ in range(180)
    )
    with pytest.raises(ValidationError, match="encoded submission exceeds"):
        HumanReviewSubmission(
            request_id=uuid4(), reviewer="huicod", snapshot_updated_at=NOW, decisions=decisions
        )


def test_policy_resolves_complete_batch_without_changing_evidence_or_source():
    task_id = uuid4()
    first = finding(task_id, label="AJ", status="confirmed", source="detector", score=0.91)
    second = finding(task_id, label="OTHER", source="vlm", score=None)
    third = finding(task_id, label="BX", source="open_detector", score=0.62)
    snapshot = waiting_snapshot(
        coverage=CoverageSummary(sufficient_coverage=True, sufficient_quality=True),
        findings=(first, second, third),
        reason="画面边缘有限",
    )
    body = submission(
        snapshot,
        review(first, "reject"),
        review(second, "confirm", open_label="树根侵入"),
        review(third, "relabel", label="FS"),
        audit=True,
        note="逐项核验",
    )

    after = resolve_human_review(snapshot, body, now=NOW + timedelta(minutes=1))

    assert after.runtime_status == "completed"
    assert after.inspection_result == "defects_found"
    assert after.stage == "finished"
    assert after.stage_progress == after.overall_progress == 1
    assert after.active_tool is None
    assert after.can_cancel is False
    assert after.updated_at == NOW + timedelta(minutes=1)
    assert after.coverage == CoverageSummary(
        sufficient_coverage=True,
        sufficient_quality=True,
        negative_audit_complete=True,
    )
    rejected, confirmed, relabeled = after.findings
    assert rejected.model_dump(exclude={"review_status", "confirmed_by"}) == first.model_dump(
        exclude={"review_status", "confirmed_by"}
    )
    assert rejected.review_status == "rejected" and rejected.confirmed_by is None
    assert confirmed.source == second.source
    assert confirmed.evidence == second.evidence
    assert confirmed.canonical_label == "OTHER"
    assert confirmed.open_label == "树根侵入"
    assert confirmed.review_status == "confirmed" and confirmed.confirmed_by == "human"
    assert relabeled.canonical_label == "FS"
    assert relabeled.open_label is None
    assert relabeled.source == "human" and relabeled.score is None
    assert relabeled.evidence == third.evidence
    assert "画面边缘有限" in after.waiting_reason
    assert "逐项核验" in after.waiting_reason


def test_policy_all_rejected_requires_audited_adequate_evidence_for_normal():
    snapshot = waiting_snapshot(
        coverage=CoverageSummary(sufficient_coverage=True, sufficient_quality=True)
    )
    decision = review(snapshot.findings[0], "reject")

    incomplete = resolve_human_review(snapshot, submission(snapshot, decision), now=NOW)
    complete = resolve_human_review(snapshot, submission(snapshot, decision, audit=True), now=NOW)

    assert incomplete.runtime_status == "partial"
    assert incomplete.inspection_result == "inconclusive"
    assert complete.runtime_status == "completed"
    assert complete.inspection_result == "normal"


def test_policy_confirmed_defect_with_inadequate_coverage_remains_partial():
    snapshot = waiting_snapshot()
    after = resolve_human_review(
        snapshot, submission(snapshot, review(snapshot.findings[0], "confirm")), now=NOW
    )
    assert after.runtime_status == "partial"
    assert after.inspection_result == "defects_found"


@pytest.mark.parametrize("mutation", ["state", "timestamp", "missing", "foreign"])
def test_policy_rejects_wrong_state_stale_or_nonexact_decisions(mutation):
    snapshot = waiting_snapshot()
    target = snapshot.findings[0]
    body = submission(snapshot, review(target, "reject"))
    if mutation == "state":
        snapshot = TaskSnapshot.model_validate(
            snapshot.model_dump()
            | {
                "runtime_status": "partial",
                "stage": "finished",
                "can_cancel": False,
            }
        )
    elif mutation == "timestamp":
        body = HumanReviewSubmission.model_validate(
            body.model_dump() | {"snapshot_updated_at": NOW - timedelta(seconds=1)}
        )
    elif mutation == "missing":
        body = HumanReviewSubmission.model_validate(body.model_dump() | {"decisions": ()})
    else:
        body = HumanReviewSubmission.model_validate(
            body.model_dump()
            | {"decisions": (review(finding(snapshot.task_id), "reject").model_dump(mode="json"),)}
        )
    with pytest.raises(HumanReviewConflict):
        resolve_human_review(snapshot, body, now=NOW)


def test_policy_requires_existing_label_and_human_other_name_on_confirmation():
    task_id = uuid4()
    unlabeled = finding(task_id, label="AJ").model_dump()
    unlabeled["canonical_label"] = None
    snapshot = waiting_snapshot(findings=(Finding.model_validate(unlabeled),))
    with pytest.raises(HumanReviewConflict):
        resolve_human_review(
            snapshot, submission(snapshot, review(snapshot.findings[0], "confirm")), now=NOW
        )

    other = finding(task_id, label="OTHER")
    snapshot = waiting_snapshot(findings=(other,))
    with pytest.raises(HumanReviewConflict):
        resolve_human_review(snapshot, submission(snapshot, review(other, "confirm")), now=NOW)


class StarterStore:
    def __init__(self, snapshot, *, record=None, records=None, version="human-review-v1"):
        self.record = record
        self.records = iter(records) if records is not None else None
        self.task = SimpleNamespace(snapshot=snapshot)
        self.run = {"config": {"human_review_version": version}} if version else None

    def get_human_review(self, task_id):
        if self.records is not None:
            return next(self.records)
        return self.record

    def get_task(self, task_id):
        return self.task

    def get_run(self, task_id):
        return self.run


@pytest.mark.asyncio
async def test_starter_returns_identical_cached_record_without_temporal_connection():
    snapshot = waiting_snapshot()
    body = submission(snapshot, review(snapshot.findings[0], "reject"), audit=True)
    record = HumanReviewRecord(
        task_id=snapshot.task_id,
        submission=body,
        before=snapshot,
        after=resolve_human_review(snapshot, body, now=NOW),
        reviewed_at=NOW,
    )
    connect = AsyncMock()
    store = StarterStore(snapshot, record=record)

    assert (
        await HumanReviewStarter(Settings(_env_file=None), store, connect=connect).submit(
            snapshot.task_id, body
        )
        == record
    )
    connect.assert_not_awaited()
    changed = HumanReviewSubmission.model_validate(body.model_dump() | {"note": "changed"})
    with pytest.raises(SubmissionConflict):
        await HumanReviewStarter(Settings(_env_file=None), store, connect=connect).submit(
            snapshot.task_id, changed
        )


@pytest.mark.asyncio
async def test_starter_closes_cache_to_terminal_read_race_before_rejecting():
    snapshot = waiting_snapshot()
    body = submission(snapshot, review(snapshot.findings[0], "reject"), audit=True)
    record = HumanReviewRecord(
        task_id=snapshot.task_id,
        submission=body,
        before=snapshot,
        after=resolve_human_review(snapshot, body, now=NOW),
        reviewed_at=NOW,
    )
    connect = AsyncMock()
    store = StarterStore(record.after, records=(None, record))

    assert (
        await HumanReviewStarter(Settings(_env_file=None), store, connect=connect).submit(
            snapshot.task_id, body
        )
        == record
    )
    connect.assert_not_awaited()


@pytest.mark.asyncio
async def test_starter_gates_state_and_pin_then_uses_named_update_payload():
    snapshot = waiting_snapshot()
    body = submission(snapshot, review(snapshot.findings[0], "reject"), audit=True)
    expected = HumanReviewRecord(
        task_id=snapshot.task_id,
        submission=body,
        before=snapshot,
        after=resolve_human_review(snapshot, body, now=NOW),
        reviewed_at=NOW,
    )
    handle = SimpleNamespace(
        execute_update=AsyncMock(
            return_value={
                "task_id": str(expected.task_id),
                "request_id": str(expected.submission.request_id),
                "runtime_status": expected.after.runtime_status,
                "inspection_result": expected.after.inspection_result,
            }
        )
    )
    client = SimpleNamespace(get_workflow_handle=lambda workflow_id: handle)
    connect = AsyncMock(return_value=client)
    store = StarterStore(snapshot, records=(None, expected))

    actual = await HumanReviewStarter(Settings(_env_file=None), store, connect=connect).submit(
        snapshot.task_id, body
    )

    assert actual == expected
    handle.execute_update.assert_awaited_once_with("submit_review", body.model_dump(mode="json"))
    assert client.get_workflow_handle(f"inspection:{snapshot.task_id}") is handle

    for store in (StarterStore(snapshot, version=None), StarterStore(expected.after)):
        with pytest.raises(HumanReviewConflict) as error:
            await HumanReviewStarter(Settings(_env_file=None), store, connect=connect).submit(
                snapshot.task_id, body
            )
        assert error.value.code == "review_not_waiting"


@pytest.mark.asyncio
async def test_starter_translates_known_update_conflicts_and_hides_transport_details():
    snapshot = waiting_snapshot()
    body = submission(snapshot, review(snapshot.findings[0], "reject"))
    app_error = ApplicationError(
        "internal workflow detail", type="review_in_progress", non_retryable=True
    )
    workflow_error = WorkflowUpdateFailedError(app_error)
    for raised, code in ((workflow_error, "review_in_progress"), (OSError("secret"), None)):
        handle = SimpleNamespace(execute_update=AsyncMock(side_effect=raised))
        client = SimpleNamespace(get_workflow_handle=lambda _: handle)
        starter = HumanReviewStarter(
            Settings(_env_file=None),
            StarterStore(snapshot),
            connect=AsyncMock(return_value=client),
        )
        if code:
            with pytest.raises(HumanReviewConflict) as error:
                await starter.submit(snapshot.task_id, body)
            assert error.value.code == code
            assert "internal workflow detail" not in str(error.value)
        else:
            with pytest.raises(InspectionStartError) as error:
                await starter.submit(snapshot.task_id, body)
            assert error.value.status_code == 503
            assert "secret" not in str(error.value)


@pytest.mark.asyncio
async def test_starter_rejects_noncompact_update_receipt():
    snapshot = waiting_snapshot()
    body = submission(snapshot, review(snapshot.findings[0], "reject"))
    record = HumanReviewRecord(
        task_id=snapshot.task_id,
        submission=body,
        before=snapshot,
        after=resolve_human_review(snapshot, body, now=NOW),
        reviewed_at=NOW,
    )
    receipt = {
        "task_id": str(snapshot.task_id),
        "request_id": str(body.request_id),
        "runtime_status": record.after.runtime_status,
        "inspection_result": record.after.inspection_result,
        "before": snapshot.model_dump(mode="json"),
    }
    handle = SimpleNamespace(execute_update=AsyncMock(return_value=receipt))
    client = SimpleNamespace(get_workflow_handle=lambda _: handle)
    starter = HumanReviewStarter(
        Settings(_env_file=None),
        StarterStore(snapshot, records=(None, record)),
        connect=AsyncMock(return_value=client),
    )

    with pytest.raises(InspectionStartError) as error:
        await starter.submit(snapshot.task_id, body)
    assert error.value.code == "workflow_unavailable"
