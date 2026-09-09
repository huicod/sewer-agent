"""Opt-in host restart test; never run this against a shared/production Temporal host."""

import asyncio
import json
import os
import subprocess
import sys
import warnings
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from pipe_inspector.config import Settings
from temporalio.client import Client, WorkflowExecutionStatus

ROOT = Path(__file__).resolve().parents[3]


def assert_local_targets(settings: Settings) -> None:
    assert settings.temporal_address == "127.0.0.1:7233", "Only the local test Temporal is allowed"
    assert settings.temporal_namespace == "default", "Unexpected test namespace"
    parameters = psycopg.conninfo.conninfo_to_dict(settings.database_dsn.get_secret_value())
    expected = {"host": "127.0.0.1", "port": "5432", "dbname": "sewer_agent", "user": "sewer_agent"}
    assert all(parameters.get(key) == value for key, value in expected.items()), (
        "Restart test requires the dedicated local sewer_agent database"
    )


def assert_dedicated_cluster() -> None:
    result = subprocess.run(
        ["pg_lsclusters", "--json"], check=True, capture_output=True, text=True, timeout=10
    )
    cluster = next(
        item
        for item in json.loads(result.stdout)
        if str(item["version"]) == "14" and item["cluster"] == "main"
    )
    assert cluster["pgdata"] == "/var/lib/postgresql/14/main"
    assert str(cluster["port"]) == "5432"
    result = subprocess.run(
        [
            "runuser",
            "-u",
            "postgres",
            "--",
            "psql",
            "-XAt",
            "-p",
            "5432",
            "-c",
            "SELECT datname FROM pg_database WHERE NOT datistemplate",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert set(result.stdout.splitlines()) == {
        "postgres",
        "sewer_agent",
        "temporal",
        "temporal_visibility",
    }, "Refusing to restart a cluster containing unrelated databases"


@pytest.mark.parametrize(
    "override",
    [
        {"temporal_address": "remote.example:7233"},
        {"temporal_namespace": "production"},
        {"database_dsn": "postgresql://sewer_agent@remote.example:5432/sewer_agent"},
    ],
)
def test_restart_guard_rejects_nonlocal_configuration(override):
    with pytest.raises(AssertionError):
        assert_local_targets(Settings(_env_file=None, **override))


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("SEWER_RUN_RESTART_TEST") != "1",
    reason="explicit opt-in required: restarts the local project Temporal and PostgreSQL services",
)
async def test_human_wait_survives_worker_temporal_and_postgres_restart():
    from backend.tests.integration.probe_workflow import DurableProbe

    settings = Settings()
    assert_local_targets(settings)
    assert_dedicated_cluster()
    probe_id = f"m0-recovery-{uuid4()}"
    queue = f"{probe_id}-queue"
    async with await psycopg.AsyncConnection.connect(
        settings.database_dsn.get_secret_value()
    ) as conn:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS sewer_runtime_probe ("
            "probe_id text, stage text, writes integer NOT NULL, PRIMARY KEY(probe_id, stage))"
        )

    def start_worker():
        return subprocess.Popen(
            [sys.executable, "-m", "backend.tests.integration.probe_worker", queue], cwd=ROOT
        )

    worker = start_worker()
    runtime_interrupted = False
    try:
        client = await Client.connect(
            settings.temporal_address, namespace=settings.temporal_namespace
        )
        handle = await client.start_workflow(
            DurableProbe.run, probe_id, id=probe_id, task_queue=queue
        )
        async with asyncio.timeout(45):
            while (
                await handle.query("stage", rpc_timeout=timedelta(seconds=15)) != "waiting_review"
            ):
                await asyncio.sleep(0.1)
        assert (await handle.describe()).status == WorkflowExecutionStatus.RUNNING

        # This process belongs exclusively to this test; kill it without graceful cleanup.
        worker.kill()
        await asyncio.to_thread(worker.wait, timeout=10)
        runtime_interrupted = True
        await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "scripts/runtime.py", "stop"],
            cwd=ROOT,
            check=True,
            timeout=30,
        )
        await asyncio.to_thread(
            subprocess.run,
            ["pg_ctlcluster", "--skip-systemctl-redirect", "14", "main", "restart"],
            check=True,
            timeout=30,
        )
        await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "scripts/runtime.py", "start"],
            cwd=ROOT,
            check=True,
            timeout=60,
        )
        runtime_interrupted = False
        client = await Client.connect(
            settings.temporal_address, namespace=settings.temporal_namespace
        )
        handle = client.get_workflow_handle(probe_id)
        assert (await handle.describe()).status == WorkflowExecutionStatus.RUNNING
        worker = start_worker()
        assert await handle.query("stage", rpc_timeout=timedelta(seconds=30)) == "waiting_review"
        await handle.signal("approve")
        async with asyncio.timeout(45):
            assert await handle.result() == {"probe_id": probe_id, "stage": "completed"}
        async with await psycopg.AsyncConnection.connect(
            settings.database_dsn.get_secret_value()
        ) as conn:
            rows = await (
                await conn.execute(
                    "SELECT stage, writes FROM sewer_runtime_probe "
                    "WHERE probe_id=%s ORDER BY stage",
                    (probe_id,),
                )
            ).fetchall()
        assert rows == [("completed", 1), ("detected", 1)]
    finally:
        try:
            if worker.poll() is None:
                worker.terminate()
                await asyncio.to_thread(worker.wait, timeout=10)
        finally:
            if runtime_interrupted:
                # start ensures PG is running and checks Temporal readiness.
                # Preserve the original test failure if recovery itself fails.
                try:
                    await asyncio.to_thread(
                        subprocess.run,
                        [sys.executable, "scripts/runtime.py", "start"],
                        cwd=ROOT,
                        check=True,
                        timeout=60,
                        capture_output=True,
                    )
                except (subprocess.SubprocessError, OSError):
                    warnings.warn(
                        "Test runtime recovery failed; inspect scripts/runtime.py status",
                        stacklevel=1,
                    )
