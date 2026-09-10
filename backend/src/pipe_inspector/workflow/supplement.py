"""Replay-safe linked image re-review; detector evidence was frozen at admission."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError


@workflow.defn
class ImageSupplementWorkflow:
    def __init__(self):
        self.stage = "queued"

    @workflow.run
    async def run(self, run: dict) -> dict:
        error = None
        self.stage = "verifying"
        try:
            await workflow.execute_activity(
                "verify_image",
                run,
                start_to_close_timeout=timedelta(seconds=run["config"]["api_timeout_seconds"] + 30),
                retry_policy=RetryPolicy(
                    maximum_attempts=3,
                    initial_interval=timedelta(seconds=1),
                    maximum_interval=timedelta(seconds=5),
                ),
            )
        except ActivityError as exc:
            cause = exc.cause
            error = cause.type if isinstance(cause, ApplicationError) else "activity_interrupted"
        self.stage = "reporting"
        result = await workflow.execute_activity(
            "finish_image_supplement",
            {"run": run, "error": error},
            start_to_close_timeout=timedelta(seconds=20),
            retry_policy=RetryPolicy(
                maximum_attempts=0,
                initial_interval=timedelta(seconds=1),
                maximum_interval=timedelta(seconds=30),
            ),
        )
        self.stage = "finished"
        return result

    @workflow.query
    def current_stage(self) -> str:
        return self.stage
