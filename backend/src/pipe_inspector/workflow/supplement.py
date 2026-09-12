"""Replay-safe linked image re-review; detector evidence was frozen at admission."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

from pipe_inspector.workflow.enhancement import run_image_enhancement
from pipe_inspector.workflow.geometry import run_image_geometry
from pipe_inspector.workflow.human_review import HUMAN_REVIEW_VERSION, HumanReviewGate


@workflow.defn
class ImageSupplementWorkflow:
    def __init__(self):
        self.stage = "queued"
        self.review_gate = HumanReviewGate()

    @workflow.update
    async def submit_review(self, submission: dict) -> dict:
        return await self.review_gate.submit(submission)

    @workflow.update
    def request_cancel(self) -> bool:
        return self.review_gate.request_cancel()

    @workflow.run
    async def run(self, run: dict) -> dict:
        error = None
        geometry_error = None
        self.stage = "verifying"
        try:
            if "geometry" in run["config"]:
                self.stage = "tools"
                await run_image_geometry(run)
            elif "enhancement" in run["config"]:
                self.stage = "tools"
                await run_image_enhancement(run)
            else:
                await workflow.execute_activity(
                    "verify_image",
                    run,
                    start_to_close_timeout=timedelta(
                        seconds=run["config"]["api_timeout_seconds"] + 30
                    ),
                    retry_policy=RetryPolicy(
                        maximum_attempts=3,
                        initial_interval=timedelta(seconds=1),
                        maximum_interval=timedelta(seconds=5),
                    ),
                )
        except ActivityError as exc:
            cause = exc.cause
            caught = cause.type if isinstance(cause, ApplicationError) else "activity_interrupted"
            if "geometry" in run["config"]:
                geometry_error = caught
            else:
                error = caught
        self.stage = "reporting"
        result = await workflow.execute_activity(
            "finish_image_supplement",
            {"run": run, "error": error, "geometry_error": geometry_error},
            start_to_close_timeout=timedelta(seconds=20),
            retry_policy=RetryPolicy(
                maximum_attempts=0,
                initial_interval=timedelta(seconds=1),
                maximum_interval=timedelta(seconds=30),
            ),
        )
        if (
            run["config"].get("human_review_version") == HUMAN_REVIEW_VERSION
            and result["runtime_status"] == "waiting_review"
        ):
            self.stage = "human_review"
            result = await self.review_gate.wait(run)
        self.stage = "finished"
        return result

    @workflow.query
    def current_stage(self) -> str:
        return self.stage
