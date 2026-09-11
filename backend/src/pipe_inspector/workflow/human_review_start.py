"""Submit a validated human-review batch to the task's Temporal workflow."""

import asyncio
from uuid import UUID

from temporalio.client import (
    Client,
    WorkflowUpdateFailedError,
    WorkflowUpdateRPCTimeoutOrCancelledError,
)
from temporalio.exceptions import ApplicationError
from temporalio.service import RPCError

from pipe_inspector.agent.human_review import HumanReviewConflict
from pipe_inspector.contracts.human_review import HumanReviewRecord, HumanReviewSubmission
from pipe_inspector.storage.repository import SubmissionConflict
from pipe_inspector.workflow.start import InspectionStartError

HUMAN_REVIEW_VERSION = "human-review-v1"

_CONFLICT_MESSAGES = {
    "review_not_waiting": "任务当前不处于人工复核状态",
    "review_in_progress": "已有人工复核正在处理，请稍后重试",
    "review_conflict": "复核内容与当前任务不一致，请重新载入",
}


class HumanReviewStarter:
    def __init__(self, settings, repository, *, connect=Client.connect):
        self.settings, self.repo, self.connect = settings, repository, connect

    @staticmethod
    def _existing_result(existing, submission):
        if existing is None:
            return None
        if existing.submission != submission:
            raise SubmissionConflict
        return existing

    @staticmethod
    def _raise_update_conflict(error: BaseException) -> None:
        cause = error.cause if isinstance(error, WorkflowUpdateFailedError) else error
        if isinstance(cause, ApplicationError) and cause.type in _CONFLICT_MESSAGES:
            raise HumanReviewConflict(cause.type, _CONFLICT_MESSAGES[cause.type]) from None

    @staticmethod
    def _validate_receipt(receipt, task_id: UUID, request_id: UUID) -> None:
        if hasattr(receipt, "model_dump"):
            receipt = receipt.model_dump(mode="json")
        if not isinstance(receipt, dict):
            raise InspectionStartError(
                "workflow_unavailable", "人工复核结果暂未确认，请使用相同提交重试"
            )
        try:
            valid = (
                set(receipt) == {"task_id", "request_id", "runtime_status", "inspection_result"}
                and UUID(str(receipt["task_id"])) == task_id
                and UUID(str(receipt["request_id"])) == request_id
                and receipt["runtime_status"] in {"completed", "partial"}
                and receipt["inspection_result"] in {"normal", "defects_found", "inconclusive"}
            )
        except (KeyError, TypeError, ValueError):
            valid = False
        if not valid:
            raise InspectionStartError(
                "workflow_unavailable", "人工复核结果暂未确认，请使用相同提交重试"
            )

    async def submit(self, task_id: UUID, submission: HumanReviewSubmission) -> HumanReviewRecord:
        existing = await asyncio.to_thread(self.repo.get_human_review, task_id)
        if existing_result := self._existing_result(existing, submission):
            return existing_result

        task = await asyncio.to_thread(self.repo.get_task, task_id)
        run = await asyncio.to_thread(self.repo.get_run, task_id)
        if (
            task.snapshot.runtime_status != "waiting_review"
            or run is None
            or run.get("config", {}).get("human_review_version") != HUMAN_REVIEW_VERSION
        ):
            late = await asyncio.to_thread(self.repo.get_human_review, task_id)
            if existing_result := self._existing_result(late, submission):
                return existing_result
            raise HumanReviewConflict(
                "review_not_waiting", _CONFLICT_MESSAGES["review_not_waiting"]
            )

        try:
            async with asyncio.timeout(10):
                client = await self.connect(
                    self.settings.temporal_address,
                    namespace=self.settings.temporal_namespace,
                )
                handle = client.get_workflow_handle(f"inspection:{task_id}")
                receipt = await handle.execute_update(
                    "submit_review", submission.model_dump(mode="json")
                )
        except (WorkflowUpdateFailedError, ApplicationError) as error:
            self._raise_update_conflict(error)
            raise InspectionStartError(
                "workflow_unavailable", "人工复核结果暂未确认，请使用相同提交重试"
            ) from None
        except (
            RPCError,
            OSError,
            TimeoutError,
            RuntimeError,
            WorkflowUpdateRPCTimeoutOrCancelledError,
        ):
            raise InspectionStartError(
                "workflow_unavailable", "人工复核结果暂未确认，请使用相同提交重试"
            ) from None

        self._validate_receipt(receipt, task_id, submission.request_id)
        stored = await asyncio.to_thread(self.repo.get_human_review, task_id)
        if stored is None:
            raise InspectionStartError(
                "workflow_unavailable", "人工复核结果暂未确认，请使用相同提交重试"
            )
        if stored.submission != submission:
            raise SubmissionConflict
        if (
            stored.after.runtime_status != receipt["runtime_status"]
            or stored.after.inspection_result != receipt["inspection_result"]
        ):
            raise InspectionStartError(
                "workflow_unavailable", "人工复核结果暂未确认，请使用相同提交重试"
            )
        return stored
