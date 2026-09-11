"""Pure complete-batch policy for resolving a waiting task by human review."""

from datetime import datetime

from pipe_inspector.contracts.findings import Finding, ReviewStatus
from pipe_inspector.contracts.human_review import FindingReview, HumanReviewSubmission
from pipe_inspector.contracts.tasks import CoverageSummary, TaskSnapshot


class HumanReviewConflict(RuntimeError):
    """A review cannot safely be applied to the current task projection."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _resolve_finding(finding: Finding, decision: FindingReview) -> Finding:
    values = finding.model_dump()
    if decision.action == "reject":
        values.update(review_status=ReviewStatus.REJECTED, confirmed_by=None)
    elif decision.action == "confirm":
        if finding.canonical_label is None:
            raise HumanReviewConflict("review_conflict", "无标准类别的发现必须先由人工重新分类")
        if finding.canonical_label == "OTHER":
            if decision.open_label is None:
                raise HumanReviewConflict(
                    "review_conflict", "确认 OTHER 类别时必须提供人工类别名称"
                )
            values["open_label"] = decision.open_label
        elif decision.open_label is not None:
            raise HumanReviewConflict(
                "review_conflict", "只有确认 OTHER 类别时可以提供开放类别名称"
            )
        values.update(review_status=ReviewStatus.CONFIRMED, confirmed_by="human")
    else:
        values.update(
            canonical_label=decision.label,
            open_label=decision.open_label if decision.label == "OTHER" else None,
            review_status=ReviewStatus.CONFIRMED,
            confirmed_by="human",
            source="human",
            score=None,
        )
    return Finding.model_validate(values)


def resolve_human_review(
    snapshot: TaskSnapshot,
    submission: HumanReviewSubmission,
    *,
    now: datetime,
) -> TaskSnapshot:
    """Validate one complete review batch and derive its terminal projection."""

    if snapshot.runtime_status != "waiting_review":
        raise HumanReviewConflict("review_not_waiting", "任务当前不处于人工复核状态")
    if snapshot.updated_at != submission.snapshot_updated_at:
        raise HumanReviewConflict("review_conflict", "任务证据已更新，请重新载入后复核")

    findings_by_id = {finding.finding_id: finding for finding in snapshot.findings}
    decisions_by_id = {decision.finding_id: decision for decision in submission.decisions}
    if decisions_by_id.keys() != findings_by_id.keys():
        raise HumanReviewConflict("review_conflict", "复核决定必须完整且仅覆盖当前发现")

    findings = tuple(
        _resolve_finding(finding, decisions_by_id[finding.finding_id])
        for finding in snapshot.findings
    )
    coverage = CoverageSummary(
        sufficient_coverage=snapshot.coverage.sufficient_coverage,
        sufficient_quality=snapshot.coverage.sufficient_quality,
        negative_audit_complete=(
            snapshot.coverage.negative_audit_complete or submission.negative_audit_complete
        ),
    )
    confirmed = any(finding.review_status == ReviewStatus.CONFIRMED for finding in findings)
    adequate = coverage.sufficient_coverage and coverage.sufficient_quality
    if confirmed:
        runtime_status = "completed" if adequate else "partial"
        inspection_result = "defects_found"
    elif adequate and coverage.negative_audit_complete:
        runtime_status = "completed"
        inspection_result = "normal"
    else:
        runtime_status = "partial"
        inspection_result = "inconclusive"

    reason_parts = [snapshot.waiting_reason] if snapshot.waiting_reason else []
    if submission.note:
        reason_parts.append(f"人工复核备注：{submission.note}")
    return TaskSnapshot.model_validate(
        snapshot.model_dump()
        | {
            "runtime_status": runtime_status,
            "inspection_result": inspection_result,
            "stage": "finished",
            "stage_progress": 1,
            "overall_progress": 1,
            "active_tool": None,
            "can_cancel": False,
            "waiting_reason": "；".join(reason_parts) or None,
            "updated_at": now,
            "findings": findings,
            "coverage": coverage,
        }
    )
