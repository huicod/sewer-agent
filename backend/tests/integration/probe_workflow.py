"""Test-only workflow used to verify durable human waits and completed-activity replay."""

from datetime import timedelta

from temporalio import activity, workflow
from temporalio.common import RetryPolicy


@activity.defn
async def record_checkpoint(probe_id: str, stage: str) -> None:
    # Activity-only I/O: the workflow itself must remain deterministic on replay.
    import psycopg
    from pipe_inspector.config import Settings

    async with await psycopg.AsyncConnection.connect(
        Settings().database_dsn.get_secret_value()
    ) as conn:
        await conn.execute(
            "INSERT INTO sewer_runtime_probe (probe_id, stage, writes) VALUES (%s, %s, 1) "
            "ON CONFLICT (probe_id, stage) DO UPDATE "
            "SET writes = sewer_runtime_probe.writes + 1",
            (probe_id, stage),
        )


@workflow.defn
class DurableProbe:
    def __init__(self) -> None:
        self.current_stage = "created"
        self.approved = False

    @workflow.run
    async def run(self, probe_id: str) -> dict[str, str]:
        await workflow.execute_activity(
            record_checkpoint,
            args=[probe_id, "detected"],
            start_to_close_timeout=timedelta(seconds=15),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )
        self.current_stage = "waiting_review"
        await workflow.wait_condition(lambda: self.approved)
        await workflow.execute_activity(
            record_checkpoint,
            args=[probe_id, "completed"],
            start_to_close_timeout=timedelta(seconds=15),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )
        self.current_stage = "completed"
        return {"probe_id": probe_id, "stage": self.current_stage}

    @workflow.signal(name="approve")
    def approve(self) -> None:
        self.approved = True

    @workflow.query(name="stage")
    def stage(self) -> str:
        return self.current_stage
