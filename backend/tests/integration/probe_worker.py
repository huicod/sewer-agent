"""Disposable worker for the opt-in local runtime recovery test."""

import asyncio
import sys

from pipe_inspector.config import Settings
from temporalio.client import Client
from temporalio.worker import Worker

from backend.tests.integration.probe_workflow import DurableProbe, record_checkpoint


async def main() -> None:
    settings = Settings()
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    worker = Worker(
        client,
        task_queue=sys.argv[1],
        workflows=[DurableProbe],
        activities=[record_checkpoint],
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
