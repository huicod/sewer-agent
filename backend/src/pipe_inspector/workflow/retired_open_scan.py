"""Read-only activity names for pre-retirement Temporal histories.

Keep workflow command order replayable without running an open detector. Missing
checkpoints fail explicitly; finalizers retain completed evidence and report gaps.
"""

from uuid import UUID

from temporalio import activity
from temporalio.exceptions import ApplicationError


class RetiredOpenScanActivities:
    def __init__(self, repository):
        self.repo = repository

    @activity.defn(name="select_open_scan")
    def select_open_scan(self, run: dict) -> dict:
        cached = self.repo.load_step(UUID(run["task_id"]), "open_plan")
        if cached is not None:
            return cached
        raise ApplicationError(
            "开放候选补检已停用；历史结果保留，未完成步骤不再执行",
            type="tool_retired",
            non_retryable=True,
        )

    @activity.defn(name="scan_open_candidate")
    def scan_open_candidate(self, payload: dict) -> dict:
        run, index = payload["run"], payload["index"]
        cached = self.repo.load_step(UUID(run["task_id"]), f"open_scan:{index}")
        if cached is not None:
            return {"index": index, "candidate_count": len(cached["batch"]["candidates"])}
        raise ApplicationError(
            "开放候选补检已停用；历史结果保留，未完成步骤不再执行",
            type="tool_retired",
            non_retryable=True,
        )
