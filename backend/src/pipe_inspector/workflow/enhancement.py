"""Shared opt-in command sequence; no imports of activity code in the sandbox."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy


async def run_image_enhancement(run):
    retry = RetryPolicy(
        maximum_attempts=min(
            3, (run["config"].get("plan") or {}).get("budget", {}).get("max_transport_attempts", 3)
        ),
        initial_interval=timedelta(seconds=1),
        maximum_interval=timedelta(seconds=5),
    )
    await workflow.execute_activity(
        "prepare_image_enhancement",
        run,
        start_to_close_timeout=timedelta(seconds=20),
        retry_policy=RetryPolicy(maximum_attempts=10, maximum_interval=timedelta(seconds=10)),
    )
    await workflow.execute_activity(
        "enhance_image",
        run,
        task_queue=run["config"]["gpu_queue"],
        start_to_close_timeout=timedelta(seconds=180),
        retry_policy=retry,
    )
    await workflow.execute_activity(
        "review_image_enhancement",
        run,
        start_to_close_timeout=timedelta(seconds=run["config"]["api_timeout_seconds"] + 30),
        retry_policy=retry,
    )
