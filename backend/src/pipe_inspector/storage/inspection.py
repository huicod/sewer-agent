"""Durable run pins, tool attempts, checkpoints, and task projections."""

from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from pipe_inspector.contracts.intake import TaskRecord
from pipe_inspector.contracts.tasks import TaskSnapshot
from pipe_inspector.contracts.tools import ToolCall, ToolCallStatus
from pipe_inspector.storage.repository import (
    MissingRecord,
    PostgresRepository,
    SubmissionConflict,
)


class InspectionRepository(PostgresRepository):
    def initialize(self) -> None:
        super().initialize()
        tasks = self._table("sewer_tasks")
        runs = self._table("sewer_inspection_runs")
        calls = self._table("sewer_tool_calls")
        checkpoints = self._table("sewer_step_checkpoints")
        with self._connect() as connection:
            connection.execute(
                sql.SQL(
                    """
                    CREATE TABLE IF NOT EXISTS {} (
                        task_id uuid PRIMARY KEY REFERENCES {} (task_id),
                        run_id uuid UNIQUE NOT NULL,
                        config jsonb NOT NULL
                    )
                    """
                ).format(runs, tasks)
            )
            connection.execute(
                sql.SQL(
                    """
                    CREATE TABLE IF NOT EXISTS {} (
                        call_id uuid NOT NULL,
                        attempt integer NOT NULL CHECK (attempt >= 1),
                        task_id uuid NOT NULL REFERENCES {} (task_id),
                        step_id text NOT NULL CHECK (length(step_id) > 0),
                        idempotency_key text NOT NULL CHECK (length(idempotency_key) > 0),
                        record jsonb NOT NULL,
                        PRIMARY KEY (call_id, attempt)
                    )
                    """
                ).format(calls, tasks)
            )
            connection.execute(
                sql.SQL(
                    """
                    CREATE TABLE IF NOT EXISTS {} (
                        task_id uuid NOT NULL REFERENCES {} (task_id),
                        step text NOT NULL CHECK (length(step) > 0),
                        payload jsonb NOT NULL,
                        PRIMARY KEY (task_id, step)
                    )
                    """
                ).format(checkpoints, tasks)
            )

    @staticmethod
    def _run_record(row: tuple) -> dict:
        return {"run_id": str(row[0]), "task_id": str(row[1]), "config": row[2]}

    def get_run(self, task_id: UUID) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                sql.SQL("SELECT run_id, task_id, config FROM {} WHERE task_id = %s").format(
                    self._table("sewer_inspection_runs")
                ),
                (task_id,),
            ).fetchone()
        return None if row is None else self._run_record(row)

    def ensure_run(self, task_id: UUID, config: dict) -> dict:
        run_id = uuid4()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    sql.SQL(
                        """
                        INSERT INTO {} (task_id, run_id, config)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (task_id) DO NOTHING
                        RETURNING run_id, task_id, config
                        """
                    ).format(self._table("sewer_inspection_runs")),
                    (task_id, run_id, Jsonb(config)),
                ).fetchone()
                if row is None:
                    row = connection.execute(
                        sql.SQL(
                            "SELECT run_id, task_id, config FROM {} WHERE task_id = %s"
                        ).format(self._table("sewer_inspection_runs")),
                        (task_id,),
                    ).fetchone()
                if row is None:
                    raise MissingRecord
                stored = self._run_record(row)
                if stored["config"] != config:
                    raise SubmissionConflict
                return stored
        except psycopg.errors.ForeignKeyViolation as error:
            raise MissingRecord from error

    def load_step(self, task_id: UUID, step: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                sql.SQL("SELECT payload FROM {} WHERE task_id = %s AND step = %s").format(
                    self._table("sewer_step_checkpoints")
                ),
                (task_id, step),
            ).fetchone()
        return None if row is None else row[0]

    def save_step(
        self,
        task_id: UUID,
        step: str,
        payload: dict,
        call: ToolCall | None = None,
    ) -> dict:
        if not step.strip():
            raise ValueError("step must not be empty")
        if call is not None:
            if call.task_id != task_id or call.step_id != step:
                raise ValueError("call must belong to the checkpoint task and step")
            if call.status not in {ToolCallStatus.SUCCEEDED, ToolCallStatus.SKIPPED}:
                raise ValueError("checkpoint calls must be succeeded or skipped")
        try:
            with self._connect() as connection:
                if call is not None:
                    self._record_call(connection, call)
                row = connection.execute(
                    sql.SQL(
                        """
                        INSERT INTO {} (task_id, step, payload)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (task_id, step) DO NOTHING
                        RETURNING payload
                        """
                    ).format(self._table("sewer_step_checkpoints")),
                    (task_id, step, Jsonb(payload)),
                ).fetchone()
                if row is None:
                    row = connection.execute(
                        sql.SQL(
                            "SELECT payload FROM {} WHERE task_id = %s AND step = %s"
                        ).format(self._table("sewer_step_checkpoints")),
                        (task_id, step),
                    ).fetchone()
                if row is None:
                    raise MissingRecord
                return row[0]
        except psycopg.errors.ForeignKeyViolation as error:
            raise MissingRecord from error

    def _record_call(self, connection: psycopg.Connection, call: ToolCall) -> None:
        row = connection.execute(
            sql.SQL(
                """
                INSERT INTO {} AS stored (
                    call_id, attempt, task_id, step_id, idempotency_key, record
                ) VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (call_id, attempt) DO UPDATE SET record = EXCLUDED.record
                WHERE stored.task_id = EXCLUDED.task_id
                  AND stored.step_id = EXCLUDED.step_id
                  AND stored.idempotency_key = EXCLUDED.idempotency_key
                RETURNING 1
                """
            ).format(self._table("sewer_tool_calls")),
            (
                call.call_id,
                call.attempt,
                call.task_id,
                call.step_id,
                call.idempotency_key,
                Jsonb(call.model_dump(mode="json")),
            ),
        ).fetchone()
        if row is None:
            raise SubmissionConflict

    def record_call(self, call: ToolCall) -> None:
        try:
            with self._connect() as connection:
                self._record_call(connection, call)
        except psycopg.errors.ForeignKeyViolation as error:
            raise MissingRecord from error

    def list_calls(self, task_id: UUID) -> tuple[ToolCall, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                sql.SQL(
                    "SELECT record FROM {} WHERE task_id = %s "
                    "ORDER BY step_id, call_id, attempt"
                ).format(self._table("sewer_tool_calls")),
                (task_id,),
            ).fetchall()
        return tuple(ToolCall.model_validate(row[0]) for row in rows)

    def update_snapshot(self, snapshot: TaskSnapshot) -> TaskRecord:
        with self._connect() as connection:
            row = connection.execute(
                sql.SQL("SELECT record FROM {} WHERE task_id = %s FOR UPDATE").format(
                    self._table("sewer_tasks")
                ),
                (snapshot.task_id,),
            ).fetchone()
            if row is None:
                raise MissingRecord
            current = TaskRecord.model_validate(row[0])
            if current.snapshot.is_terminal:
                if current.snapshot == snapshot:
                    return current
                raise SubmissionConflict
            updated = TaskRecord.model_validate(
                current.model_dump() | {"snapshot": snapshot}
            )
            connection.execute(
                sql.SQL("UPDATE {} SET record = %s WHERE task_id = %s").format(
                    self._table("sewer_tasks")
                ),
                (Jsonb(updated.model_dump(mode="json")), snapshot.task_id),
            )
        return updated
