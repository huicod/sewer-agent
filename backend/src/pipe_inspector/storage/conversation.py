"""Durable conversation turns with per-session admission serialization."""

from datetime import datetime, timezone
from uuid import UUID

from psycopg import sql
from psycopg.types.json import Jsonb

from pipe_inspector.contracts.assets import AssetKind, AssetRecord
from pipe_inspector.contracts.intake import TaskRecord
from pipe_inspector.contracts.turns import TurnRecord, TurnSubmission
from pipe_inspector.storage.inspection import InspectionRepository
from pipe_inspector.storage.repository import MissingRecord, SubmissionConflict


class ConversationBusy(RuntimeError):
    """A session already has a queued or running turn."""

    def __init__(self) -> None:
        super().__init__("conversation already has an active turn")


class ConversationRepository(InspectionRepository):
    _UPDATABLE_FIELDS = {
        "status",
        "stage",
        "reply",
        "linked_task_id",
        "error_code",
        "attempts",
    }

    def initialize(self) -> None:
        super().initialize()
        with self._connect() as connection:
            connection.execute(
                sql.SQL(
                    """
                    CREATE TABLE IF NOT EXISTS {} (
                        request_id uuid PRIMARY KEY,
                        session_id uuid NOT NULL REFERENCES {} (session_id),
                        created_at timestamptz NOT NULL,
                        submission jsonb NOT NULL,
                        record jsonb NOT NULL,
                        config jsonb NOT NULL,
                        checkpoints jsonb NOT NULL DEFAULT '{{}}'::jsonb
                    )
                    """
                ).format(
                    self._table("sewer_conversation_turns"),
                    self._table("sewer_sessions"),
                )
            )

    def _validate_references(self, connection, submission: TurnSubmission) -> None:
        asset = None
        if submission.input_asset is not None:
            asset_row = connection.execute(
                sql.SQL(
                    "SELECT record FROM {} WHERE asset_id = %s AND version = %s"
                ).format(self._table("sewer_assets")),
                (submission.input_asset.asset_id, submission.input_asset.version),
            ).fetchone()
            if asset_row is None:
                raise MissingRecord
            asset = AssetRecord.model_validate(asset_row[0])
            if asset.kind not in {AssetKind.IMAGE, AssetKind.VIDEO} or asset.parent is not None:
                raise SubmissionConflict
            foreign_owner = connection.execute(
                sql.SQL(
                    """
                    SELECT 1 FROM {}
                    WHERE input_asset_id = %s AND input_asset_version = %s
                      AND session_id <> %s
                    LIMIT 1
                    """
                ).format(self._table("sewer_tasks")),
                (
                    submission.input_asset.asset_id,
                    submission.input_asset.version,
                    submission.session_id,
                ),
            ).fetchone()
            if foreign_owner is not None:
                raise SubmissionConflict

        if submission.task_id is None:
            return
        task_row = connection.execute(
            sql.SQL("SELECT record FROM {} WHERE task_id = %s").format(
                self._table("sewer_tasks")
            ),
            (submission.task_id,),
        ).fetchone()
        if task_row is None:
            raise MissingRecord
        task = TaskRecord.model_validate(task_row[0])
        if task.session_id != submission.session_id:
            raise SubmissionConflict
        if asset is not None and task.input_asset != asset.ref:
            raise SubmissionConflict

    def admit_turn(self, submission: TurnSubmission, config: dict) -> TurnRecord:
        created_at = datetime.now(timezone.utc)
        candidate = TurnRecord(
            **submission.model_dump(), created_at=created_at, updated_at=created_at
        )
        with self._connect() as connection:
            session = connection.execute(
                sql.SQL("SELECT 1 FROM {} WHERE session_id = %s FOR UPDATE").format(
                    self._table("sewer_sessions")
                ),
                (submission.session_id,),
            ).fetchone()
            if session is None:
                raise MissingRecord

            existing = connection.execute(
                sql.SQL("SELECT submission, record FROM {} WHERE request_id = %s").format(
                    self._table("sewer_conversation_turns")
                ),
                (submission.request_id,),
            ).fetchone()
            if existing is not None:
                if TurnSubmission.model_validate(existing[0]) != submission:
                    raise SubmissionConflict
                return TurnRecord.model_validate(existing[1])

            busy = connection.execute(
                sql.SQL(
                    """
                    SELECT 1 FROM {}
                    WHERE session_id = %s
                      AND record ->> 'status' IN ('queued', 'running')
                    LIMIT 1
                    """
                ).format(self._table("sewer_conversation_turns")),
                (submission.session_id,),
            ).fetchone()
            if busy is not None:
                raise ConversationBusy

            self._validate_references(connection, submission)
            inserted = connection.execute(
                sql.SQL(
                    """
                    INSERT INTO {} (
                        request_id, session_id, created_at, submission, record, config
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (request_id) DO NOTHING
                    RETURNING record
                    """
                ).format(self._table("sewer_conversation_turns")),
                (
                    submission.request_id,
                    submission.session_id,
                    created_at,
                    Jsonb(submission.model_dump(mode="json")),
                    Jsonb(candidate.model_dump(mode="json")),
                    Jsonb(config),
                ),
            ).fetchone()
            if inserted is None:
                existing = connection.execute(
                    sql.SQL("SELECT submission, record FROM {} WHERE request_id = %s").format(
                        self._table("sewer_conversation_turns")
                    ),
                    (submission.request_id,),
                ).fetchone()
                if existing is None:
                    raise MissingRecord
                if TurnSubmission.model_validate(existing[0]) != submission:
                    raise SubmissionConflict
                return TurnRecord.model_validate(existing[1])
        return candidate

    def get_turn(self, request_id: UUID) -> TurnRecord:
        with self._connect() as connection:
            row = connection.execute(
                sql.SQL("SELECT record FROM {} WHERE request_id = %s").format(
                    self._table("sewer_conversation_turns")
                ),
                (request_id,),
            ).fetchone()
        if row is None:
            raise MissingRecord
        return TurnRecord.model_validate(row[0])

    def list_turns(self, session_id: UUID, limit: int = 50) -> tuple[TurnRecord, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        with self._connect() as connection:
            session = connection.execute(
                sql.SQL("SELECT 1 FROM {} WHERE session_id = %s").format(
                    self._table("sewer_sessions")
                ),
                (session_id,),
            ).fetchone()
            if session is None:
                raise MissingRecord
            rows = connection.execute(
                sql.SQL(
                    """
                    SELECT record FROM (
                        SELECT request_id, created_at, record FROM {}
                        WHERE session_id = %s
                        ORDER BY created_at DESC, request_id DESC
                        LIMIT %s
                    ) AS recent
                    ORDER BY created_at, request_id
                    """
                ).format(self._table("sewer_conversation_turns")),
                (session_id, limit),
            ).fetchall()
        return tuple(TurnRecord.model_validate(row[0]) for row in rows)

    def get_turn_config(self, request_id: UUID) -> dict:
        with self._connect() as connection:
            row = connection.execute(
                sql.SQL("SELECT config FROM {} WHERE request_id = %s").format(
                    self._table("sewer_conversation_turns")
                ),
                (request_id,),
            ).fetchone()
        if row is None:
            raise MissingRecord
        return row[0]

    def load_turn_step(self, request_id: UUID, step: str) -> dict | None:
        if not step.strip():
            raise ValueError("step must not be empty")
        with self._connect() as connection:
            row = connection.execute(
                sql.SQL("SELECT checkpoints FROM {} WHERE request_id = %s").format(
                    self._table("sewer_conversation_turns")
                ),
                (request_id,),
            ).fetchone()
        if row is None:
            raise MissingRecord
        return row[0].get(step)

    def save_turn_step(self, request_id: UUID, step: str, payload: dict) -> dict:
        if not step.strip():
            raise ValueError("step must not be empty")
        with self._connect() as connection:
            row = connection.execute(
                sql.SQL("SELECT checkpoints FROM {} WHERE request_id = %s FOR UPDATE").format(
                    self._table("sewer_conversation_turns")
                ),
                (request_id,),
            ).fetchone()
            if row is None:
                raise MissingRecord
            checkpoints = row[0]
            if step in checkpoints:
                return checkpoints[step]
            checkpoints[step] = payload
            connection.execute(
                sql.SQL("UPDATE {} SET checkpoints = %s WHERE request_id = %s").format(
                    self._table("sewer_conversation_turns")
                ),
                (Jsonb(checkpoints), request_id),
            )
        return payload

    def update_turn(self, request_id: UUID, **changes) -> TurnRecord:
        unsupported = set(changes) - self._UPDATABLE_FIELDS
        if unsupported:
            raise ValueError("unsupported turn fields: " + ", ".join(sorted(unsupported)))
        with self._connect() as connection:
            row = connection.execute(
                sql.SQL("SELECT record FROM {} WHERE request_id = %s FOR UPDATE").format(
                    self._table("sewer_conversation_turns")
                ),
                (request_id,),
            ).fetchone()
            if row is None:
                raise MissingRecord
            current = TurnRecord.model_validate(row[0])
            if current.status in {"completed", "failed"}:
                return current

            linked_task_id = changes.get("linked_task_id")
            if linked_task_id is not None:
                task_row = connection.execute(
                    sql.SQL("SELECT record FROM {} WHERE task_id = %s").format(
                        self._table("sewer_tasks")
                    ),
                    (linked_task_id,),
                ).fetchone()
                if task_row is None:
                    raise MissingRecord
                task = TaskRecord.model_validate(task_row[0])
                if task.session_id != current.session_id:
                    raise SubmissionConflict

            if "attempts" in changes:
                changes["attempts"] = max(current.attempts, changes["attempts"])
            updated = TurnRecord.model_validate(
                current.model_dump()
                | changes
                | {"updated_at": datetime.now(timezone.utc)}
            )
            connection.execute(
                sql.SQL("UPDATE {} SET record = %s WHERE request_id = %s").format(
                    self._table("sewer_conversation_turns")
                ),
                (Jsonb(updated.model_dump(mode="json")), request_id),
            )
        return updated
