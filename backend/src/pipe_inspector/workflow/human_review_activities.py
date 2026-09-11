"""Durable human decisions; these activities never invoke a model."""

from datetime import datetime, timezone
from uuid import UUID

from pydantic import ValidationError
from temporalio import activity
from temporalio.exceptions import ApplicationError

from pipe_inspector.agent.human_review import HumanReviewConflict
from pipe_inspector.contracts.human_review import HumanReviewSubmission
from pipe_inspector.contracts.tasks import TaskSnapshot
from pipe_inspector.storage.repository import MissingRecord, SubmissionConflict


class HumanReviewActivities:
    def __init__(self, repository):
        self.repo = repository

    @activity.defn(name="apply_human_review")
    def apply_human_review(self, payload: dict) -> dict:
        try:
            task_id = UUID(payload["task_id"])
            submission = HumanReviewSubmission.model_validate(payload["submission"])
            record = self.repo.apply_human_review(task_id, submission)
        except HumanReviewConflict as exc:
            raise ApplicationError(exc.code, type=exc.code, non_retryable=True) from None
        except SubmissionConflict:
            raise ApplicationError(
                "review_conflict", type="review_conflict", non_retryable=True
            ) from None
        except (ValidationError, ValueError, KeyError, MissingRecord):
            raise ApplicationError(
                "review_invalid", type="review_invalid", non_retryable=True
            ) from None
        return {
            "task_id": str(record.task_id),
            "request_id": str(record.submission.request_id),
            "runtime_status": record.after.runtime_status.value,
            "inspection_result": record.after.inspection_result.value,
        }

    @activity.defn(name="cancel_human_review")
    def cancel_human_review(self, payload: dict) -> dict:
        task_id = UUID(payload["task_id"])
        current = self.repo.get_task(task_id).snapshot
        if not current.is_terminal:
            if current.runtime_status != "waiting_review":
                raise ApplicationError(
                    "review_not_waiting", type="review_not_waiting", non_retryable=True
                )
            cached = self.repo.load_step(task_id, "human_review_cancel")
            if cached is None:
                snapshot = TaskSnapshot.model_validate(
                    current.model_dump()
                    | {
                        "runtime_status": "cancelled",
                        "inspection_result": "inconclusive"
                        if current.inspection_result == "normal"
                        else current.inspection_result,
                        "stage": "finished",
                        "stage_progress": 1,
                        "overall_progress": 1,
                        "can_cancel": False,
                        "active_tool": None,
                        "updated_at": datetime.now(timezone.utc),
                        "waiting_reason": (current.waiting_reason or "") + "；人工审核等待已取消",
                    }
                )
                cached = self.repo.save_step(
                    task_id, "human_review_cancel", snapshot.model_dump(mode="json")
                )
            current = TaskSnapshot.model_validate(cached)
            self.repo.update_snapshot(current)
        return {
            "task_id": str(task_id),
            "runtime_status": current.runtime_status.value,
            "inspection_result": current.inspection_result.value,
        }
