"""Durable video orchestration; frame observations stay in PostgreSQL, not history."""

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError, CancelledError

from pipe_inspector.workflow.human_review import HUMAN_REVIEW_VERSION, HumanReviewGate


@workflow.defn
class VideoInspectionWorkflow:
    def __init__(self):
        self.stage = "queued"
        self.cancel_requested = False
        self.active = None
        self.review_gate = HumanReviewGate()

    @workflow.update
    async def submit_review(self, submission: dict) -> dict:
        return await self.review_gate.submit(submission)

    @workflow.update
    def request_cancel(self):
        if self.stage == "human_review":
            return self.review_gate.request_cancel()
        if self.stage in {"reporting", "finished"}:
            return False
        self.cancel_requested = True
        if self.active is not None:
            self.active.cancel()
        return True

    @workflow.query
    def current_stage(self) -> str:
        return self.stage

    async def _execute(self, name, payload, *, retry_policy=None, **options):
        if self.cancel_requested:
            raise CancelledError()
        self.active = workflow.start_activity(
            name,
            payload,
            cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
            retry_policy=retry_policy
            or RetryPolicy(
                maximum_attempts=3,
                initial_interval=timedelta(seconds=1),
                maximum_interval=timedelta(seconds=5),
            ),
            **options,
        )
        try:
            return await self.active
        finally:
            self.active = None

    @staticmethod
    def _error(exc):
        cause = exc.cause
        return cause.type if isinstance(cause, ApplicationError) else "activity_interrupted"

    @workflow.run
    async def run(self, run: dict) -> dict:
        error, review_errors = None, {}
        try:
            self.stage = "detecting"
            await self._execute(
                "scan_video",
                run,
                task_queue=run["config"]["gpu_queue"],
                start_to_close_timeout=timedelta(hours=12),
                schedule_to_close_timeout=timedelta(hours=36),
                heartbeat_timeout=timedelta(seconds=30),
            )
            self.stage = "aggregating"
            selected = await self._execute(
                "prepare_video",
                run,
                start_to_close_timeout=timedelta(hours=12),
                heartbeat_timeout=timedelta(seconds=30),
            )
            self.stage = "verifying"
            for index in selected["review_frames"]:
                try:
                    await self._execute(
                        "verify_video_frame",
                        {"run": run, "frame_index": index},
                        start_to_close_timeout=timedelta(
                            seconds=run["config"]["api_timeout_seconds"] + 30
                        ),
                    )
                except ActivityError as exc:
                    if self.cancel_requested:
                        raise CancelledError() from None
                    review_errors[str(index)] = self._error(exc)
            if run["config"].get("open_scan"):
                self.stage = "tools"
                selected = await self._execute(
                    "select_open_scan", run, start_to_close_timeout=timedelta(seconds=60)
                )
                for index in range(len(selected["selected"])):
                    try:
                        await self._execute(
                            "scan_open_candidate",
                            {"run": run, "index": index},
                            task_queue=run["config"]["gpu_queue"],
                            start_to_close_timeout=timedelta(seconds=180),
                            retry_policy=RetryPolicy(
                                maximum_attempts=(run["config"].get("plan") or {})
                                .get("budget", {})
                                .get("max_transport_attempts", 3),
                                initial_interval=timedelta(seconds=1),
                                maximum_interval=timedelta(seconds=5),
                            ),
                        )
                    except ActivityError as exc:
                        if self.cancel_requested:
                            raise CancelledError() from None
                        error = error or self._error(exc)
        except ActivityError as exc:
            error = self._error(exc)
        except (CancelledError, asyncio.CancelledError):
            error = "cancelled"
        self.stage = "reporting"
        # This replayed transition closes cancellation admission before final commit.
        # Final projection retries do not repeat detection, frame extraction or paid calls.
        result = await workflow.execute_activity(
            "finish_video",
            {
                "run": run,
                "error": error,
                "review_errors": review_errors,
                "cancelled": self.cancel_requested,
            },
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=RetryPolicy(maximum_attempts=0, maximum_interval=timedelta(seconds=30)),
        )
        if (
            run["config"].get("human_review_version") == HUMAN_REVIEW_VERSION
            and result["runtime_status"] == "waiting_review"
        ):
            self.stage = "human_review"
            result = await self.review_gate.wait(run)
        self.stage = "finished"
        return result
