"""Deterministic waiting gate shared by inspection workflows."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

HUMAN_REVIEW_VERSION = "human-review-v1"


class HumanReviewGate:
    def __init__(self):
        self.run = None
        self.waiting = False
        self.cancelled = False
        self.inflight = False
        self.submission = None
        self.receipt = None

    async def wait(self, run: dict) -> dict:
        self.run = run
        self.waiting = True
        await workflow.wait_condition(lambda: self.receipt is not None or self.cancelled)
        self.waiting = False
        if self.cancelled:
            result = await workflow.execute_activity(
                "cancel_human_review",
                {"task_id": run["task_id"]},
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_interval=timedelta(seconds=30)),
            )
        else:
            result = self.receipt
        await workflow.wait_condition(workflow.all_handlers_finished)
        return result

    async def submit(self, submission: dict) -> dict:
        if self.receipt is not None:
            if submission != self.submission:
                raise ApplicationError("review_conflict", type="review_conflict")
            return self.receipt
        if not self.waiting or self.cancelled:
            raise ApplicationError("review_not_waiting", type="review_not_waiting")
        while self.inflight:
            if submission != self.submission:
                raise ApplicationError("review_in_progress", type="review_in_progress")
            await workflow.wait_condition(lambda: not self.inflight)
            if self.receipt is not None:
                return self.receipt
        if not self.waiting or self.cancelled:
            raise ApplicationError("review_not_waiting", type="review_not_waiting")
        self.inflight = True
        self.submission = submission
        try:
            self.receipt = await workflow.execute_activity(
                "apply_human_review",
                {"task_id": self.run["task_id"], "submission": submission},
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_interval=timedelta(seconds=30)),
            )
            return self.receipt
        except ActivityError as exc:
            code = getattr(exc.cause, "type", None)
            if code in {"review_conflict", "review_not_waiting", "review_invalid"}:
                raise ApplicationError(code, type=code) from None
            raise
        finally:
            self.inflight = False

    def request_cancel(self) -> bool:
        if not self.waiting or self.inflight or self.receipt is not None:
            return False
        self.cancelled = True
        return True
