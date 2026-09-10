"""Re-review decisions retain earlier unresolved evidence and substantive conflicts."""

from uuid import uuid4

from pipe_inspector.agent.recall_policy import decide_image_recall
from pipe_inspector.contracts.findings import Finding
from pipe_inspector.contracts.inspection import InspectionDecision

SUPPLEMENT_POLICY_VERSION = "supplement-policy-v2"


def _opinion(candidate, original_label):
    if candidate.verdict == "reject":
        return ("reject", None)
    if candidate.verdict in {"accept", "revise"}:
        return ("positive", candidate.proposed_label or original_label)
    return None


def decide_supplement(
    task_id, asset, observation, review, *, previous, previous_review, review_error=None
):
    fresh = decide_image_recall(task_id, asset, observation, review, review_error=review_error)
    findings = list(fresh.findings)
    reasons = list(fresh.reasons)
    if (
        review_error is None
        and review is not None
        and review.image_assessable
        and previous_review is not None
        and previous_review.image_assessable
    ):
        before = {item.index: item for item in previous_review.candidates}
        for item in review.candidates:
            if item.index not in before:
                continue
            label = observation.detection.detections[item.index].label
            earlier, later = _opinion(before[item.index], label), _opinion(item, label)
            if earlier is not None and later is not None and earlier != later:
                findings[item.index] = Finding.model_validate(
                    findings[item.index].model_dump()
                    | {
                        "review_status": "needs_review",
                        "confirmed_by": None,
                    }
                )
                if "prior_review_conflict" not in reasons:
                    reasons.append("prior_review_conflict")
    for old in previous.findings:
        # Detector candidates were reviewed by original index above. Open/VLM evidence
        # cannot be dismissed merely because a later full-image answer omits it.
        if old.source == "detector" or old.review_status == "rejected":
            continue
        if not any(
            item.source == old.source
            and item.canonical_label == old.canonical_label
            and item.open_label == old.open_label
            and item.evidence == old.evidence
            for item in findings
        ):
            findings.append(
                Finding.model_validate(
                    old.model_dump()
                    | {
                        "finding_id": uuid4(),
                        "task_id": task_id,
                    }
                )
            )
        if (
            old.review_status in {"tentative", "needs_review"}
            and "prior_anomaly_requires_review" not in reasons
        ):
            reasons.append("prior_anomaly_requires_review")
    unresolved = any(item.review_status in {"tentative", "needs_review"} for item in findings)
    if any(item.review_status == "confirmed" for item in findings):
        result = "defects_found"
    elif not reasons and not unresolved and fresh.inspection_result == "normal":
        result = "normal"
    else:
        result = "inconclusive"
    return InspectionDecision(
        findings=tuple(findings),
        coverage=fresh.coverage,
        inspection_result=result,
        needs_review=bool(reasons) or unresolved,
        reasons=tuple(reasons),
    )
