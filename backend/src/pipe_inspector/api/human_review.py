"""Read and submit complete-batch human reviews for inspection tasks."""

from uuid import UUID

from fastapi import APIRouter

from pipe_inspector.contracts.human_review import (
    HumanReviewDetail,
    HumanReviewRecord,
    HumanReviewSubmission,
)
from pipe_inspector.workflow.human_review_start import HUMAN_REVIEW_VERSION


def human_review_router(repository, starter) -> APIRouter:
    router = APIRouter(prefix="/api/tasks", tags=["human-review"])

    @router.get("/{task_id}/review", response_model=HumanReviewDetail)
    def get_review(task_id: UUID):
        task = repository.get_task(task_id)
        run = repository.get_run(task_id)
        record = repository.get_human_review(task_id)
        # The atomic audit may commit between the task and audit reads. Its immutable
        # after-snapshot is authoritative, even when the earlier read still waited.
        snapshot = record.after if record is not None else task.snapshot
        can_review = bool(
            record is None
            and snapshot.runtime_status == "waiting_review"
            and run is not None
            and run.get("config", {}).get("human_review_version") == HUMAN_REVIEW_VERSION
        )
        return HumanReviewDetail(
            task_id=task_id,
            can_review=can_review,
            snapshot=snapshot,
            record=record,
        )

    @router.post("/{task_id}/review", response_model=HumanReviewRecord)
    async def submit_review(task_id: UUID, body: HumanReviewSubmission):
        return await starter.submit(task_id, body)

    return router
