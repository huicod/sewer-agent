"""Replay-safe geometry activity sequence; model and storage work stays in activities."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy


async def run_image_geometry(run: dict) -> None:
    geometry = run["config"]["geometry"]
    operation = geometry["options"]["operation"]
    retry = RetryPolicy(
        maximum_attempts=min(
            3,
            (run["config"].get("plan") or {})
            .get("budget", {})
            .get("max_transport_attempts", 3),
        ),
        initial_interval=timedelta(seconds=1),
        maximum_interval=timedelta(seconds=5),
    )
    await workflow.execute_activity(
        "prepare_image_geometry",
        run,
        start_to_close_timeout=timedelta(seconds=20),
        retry_policy=RetryPolicy(maximum_attempts=10, maximum_interval=timedelta(seconds=10)),
    )
    if operation in {"segment", "relative_measure"}:
        await workflow.execute_activity(
            "segment_image",
            run,
            task_queue=run["config"]["gpu_queue"],
            start_to_close_timeout=timedelta(seconds=180),
            retry_policy=retry,
        )
    if operation in {"relative_depth", "relative_measure"}:
        await workflow.execute_activity(
            "estimate_relative_depth",
            run,
            task_queue=run["config"]["gpu_queue"],
            start_to_close_timeout=timedelta(seconds=180),
            retry_policy=retry,
        )
