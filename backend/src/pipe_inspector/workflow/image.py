"""Replay-safe image workflow. No filesystem, database, models or credentials here."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

from pipe_inspector.workflow.enhancement import run_image_enhancement
from pipe_inspector.workflow.human_review import HUMAN_REVIEW_VERSION, HumanReviewGate


@workflow.defn
class ImageInspectionWorkflow:
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
        storage_retry = RetryPolicy(maximum_attempts=10, maximum_interval=timedelta(seconds=10))
        finalize_retry = RetryPolicy(
            maximum_attempts=0,
            initial_interval=timedelta(seconds=1),
            backoff_coefficient=2,
            maximum_interval=timedelta(seconds=30),
        )
        try:
            self.stage = "detecting"
            await workflow.execute_activity(
                "publish_image_stage",
                {"run": run, "stage": self.stage},
                start_to_close_timeout=timedelta(seconds=20),
                retry_policy=storage_retry,
            )
            await workflow.execute_activity(
                "detect_image",
                run,
                task_queue=run["config"]["gpu_queue"],
                start_to_close_timeout=timedelta(seconds=180),
                retry_policy=RetryPolicy(
                    maximum_attempts=3,
                    initial_interval=timedelta(seconds=1),
                    maximum_interval=timedelta(seconds=5),
                ),
            )
            self.stage = "verifying"
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
        # No new commands for histories admitted before the opt-in configuration existed.
        if run["config"].get("open_scan") and error != "configuration_changed":
            self.stage = "tools"
            try:
                selected = await workflow.execute_activity(
                    "select_open_scan",
                    run,
                    start_to_close_timeout=timedelta(seconds=60),
                    retry_policy=storage_retry,
                )
                for index in range(len(selected["selected"])):
                    try:
                        await workflow.execute_activity(
                            "scan_open_candidate",
                            {"run": run, "index": index},
                            task_queue=run["config"]["gpu_queue"],
                            start_to_close_timeout=timedelta(seconds=180),
                            retry_policy=RetryPolicy(
                                maximum_attempts=(run["config"].get("plan") or {})
                                .get("budget", {})
                                .get("max_transport_attempts", 3)
                            ),
                        )
                    except ActivityError as exc:
                        cause = exc.cause
                        error = error or (
                            cause.type
                            if isinstance(cause, ApplicationError)
                            else "open_scan_interrupted"
                        )
            except ActivityError:
                error = error or "open_selection_failed"
        if "auto_enhancement" in run["config"] and error is None:
            self.stage = "tools"
            try:
                selected = await workflow.execute_activity(
                    "select_image_enhancement",
                    run,
                    start_to_close_timeout=timedelta(
                        seconds=run["config"]["api_timeout_seconds"] + 30
                    ),
                    retry_policy=RetryPolicy(maximum_attempts=min(
                        3, (run["config"].get("plan") or {}).get("budget", {})
                        .get("max_transport_attempts", 3)
                    )),
                )
                if selected.get("enhancement") is not None:
                    run = run | {"config": run["config"] | {"enhancement": selected["enhancement"]}}
            except ActivityError as exc:
                cause = exc.cause
                error = (cause.type if isinstance(cause, ApplicationError)
                         else "enhancement_selection_interrupted")
        if "enhancement" in run["config"] and error is None:
            self.stage = "tools"
            try:
                await run_image_enhancement(run)
            except ActivityError as exc:
                cause = exc.cause
                error = (
                    cause.type if isinstance(cause, ApplicationError) else "enhancement_interrupted"
                )
        self.stage = "reporting"
        result = await workflow.execute_activity(
            "finish_image",
            {"run": run, "error": error},
            start_to_close_timeout=timedelta(seconds=20),
            retry_policy=finalize_retry,
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
