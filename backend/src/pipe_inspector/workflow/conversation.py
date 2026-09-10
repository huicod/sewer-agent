"""Durable conversation orchestration; model/tools execute only in activities."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError


@workflow.defn
class ConversationWorkflow:
    def __init__(self):
        self.stage = "queued"

    @workflow.run
    async def run(self, run: dict) -> dict:
        error = None
        try:
            self.stage = "planning"
            await workflow.execute_activity(
                "parse_conversation_turn",
                run,
                start_to_close_timeout=timedelta(seconds=run["config"]["api_timeout_seconds"] + 30),
                retry_policy=RetryPolicy(maximum_attempts=3, maximum_interval=timedelta(seconds=5)),
            )
            self.stage = "executing"
            await workflow.execute_activity(
                "dispatch_conversation_turn",
                run,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=5, maximum_interval=timedelta(seconds=5)),
            )
        except ActivityError as exc:
            error = (
                exc.cause.type
                if isinstance(exc.cause, ApplicationError)
                else "activity_interrupted"
            )
        result = await workflow.execute_activity(
            "finish_conversation_turn",
            {"run": run, "error": error},
            start_to_close_timeout=timedelta(seconds=20),
            retry_policy=RetryPolicy(maximum_attempts=0, maximum_interval=timedelta(seconds=30)),
        )
        self.stage = "finished"
        return result

    @workflow.query
    def current_stage(self) -> str:
        return self.stage
