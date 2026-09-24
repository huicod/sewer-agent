"""PostgreSQL persistence for intake records, with request-level idempotency."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
from pydantic import SecretStr

from pipe_inspector.contracts.assets import AssetKind, AssetRecord, AssetRef
from pipe_inspector.contracts.intake import (
    SessionDetail,
    SessionRecord,
    SessionSummary,
    TaskRecord,
    TaskSubmission,
)
from pipe_inspector.contracts.tasks import TaskSnapshot


class MissingRecord(LookupError):
    """A referenced or requested record does not exist."""

    def __init__(self) -> None:
        super().__init__("record not found")


class SubmissionConflict(RuntimeError):
    """An immutable identity is already bound to different content."""

    def __init__(self) -> None:
        super().__init__("submission conflicts with stored state")


class PostgresRepository:
    def __init__(self, dsn: SecretStr, *, schema: str = "public") -> None:
        if not schema:
            raise ValueError("schema must not be empty")
        self._dsn = dsn
        self._schema = schema

    def _table(self, name: str) -> sql.Identifier:
        return sql.Identifier(self._schema, name)

    def _connect(self) -> psycopg.Connection:
        try:
            return psycopg.connect(self._dsn.get_secret_value(), connect_timeout=5)
        except psycopg.OperationalError:
            raise psycopg.OperationalError("database connection failed") from None

    def initialize(self) -> None:
        sessions = self._table("sewer_sessions")
        assets = self._table("sewer_assets")
        tasks = self._table("sewer_tasks")
        with self._connect() as connection:
            connection.execute(
                sql.SQL(
                    """
                    CREATE TABLE IF NOT EXISTS {} (
                        session_id uuid PRIMARY KEY,
                        created_at timestamptz NOT NULL
                    )
                    """
                ).format(sessions)
            )
            connection.execute(
                sql.SQL(
                    """
                    CREATE TABLE IF NOT EXISTS {} (
                        asset_id uuid NOT NULL,
                        version integer NOT NULL CHECK (version >= 1),
                        parent_asset_id uuid,
                        parent_version integer,
                        record jsonb NOT NULL,
                        PRIMARY KEY (asset_id, version),
                        CHECK ((parent_asset_id IS NULL) = (parent_version IS NULL)),
                        FOREIGN KEY (parent_asset_id, parent_version)
                            REFERENCES {} (asset_id, version)
                    )
                    """
                ).format(assets, assets)
            )
            connection.execute(
                sql.SQL(
                    """
                    CREATE TABLE IF NOT EXISTS {} (
                        request_id uuid PRIMARY KEY,
                        task_id uuid UNIQUE NOT NULL,
                        session_id uuid NOT NULL REFERENCES {} (session_id),
                        input_asset_id uuid NOT NULL,
                        input_asset_version integer NOT NULL,
                        created_at timestamptz NOT NULL,
                        submission jsonb NOT NULL,
                        record jsonb NOT NULL,
                        FOREIGN KEY (input_asset_id, input_asset_version)
                            REFERENCES {} (asset_id, version)
                    )
                    """
                ).format(tasks, sessions, assets)
            )

    def create_session(self) -> SessionRecord:
        record = SessionRecord(session_id=uuid4(), created_at=datetime.now(timezone.utc))
        with self._connect() as connection:
            connection.execute(
                sql.SQL("INSERT INTO {} (session_id, created_at) VALUES (%s, %s)").format(
                    self._table("sewer_sessions")
                ),
                (record.session_id, record.created_at),
            )
        return record

    def list_sessions(self) -> tuple[SessionSummary, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                sql.SQL("""
                SELECT s.session_id, s.created_at,
                    greatest(s.created_at, tasks.updated_at, turns.updated_at) AS updated_at,
                    left(coalesce(nullif(turns.title, ''), nullif(tasks.title, ''), '新对话'), 120),
                    tasks.total, turns.total
                FROM {} s
                LEFT JOIN LATERAL (
                    SELECT count(*) AS total,
                        max((record->'snapshot'->>'updated_at')::timestamptz) AS updated_at,
                        (array_agg(record->>'instruction' ORDER BY created_at, request_id))[1]
                            AS title
                    FROM {} WHERE session_id = s.session_id
                ) tasks ON true
                LEFT JOIN LATERAL (
                    SELECT count(*) AS total,
                        max((record->>'updated_at')::timestamptz) AS updated_at,
                        (array_agg(record->>'text' ORDER BY created_at, request_id))[1] AS title
                    FROM {} WHERE session_id = s.session_id
                ) turns ON true
                ORDER BY updated_at DESC, s.session_id
            """).format(
                    self._table("sewer_sessions"),
                    self._table("sewer_tasks"),
                    self._table("sewer_conversation_turns"),
                )
            ).fetchall()
        return tuple(
            SessionSummary(
                session_id=row[0],
                created_at=row[1],
                updated_at=row[2],
                title=row[3],
                task_count=row[4],
                turn_count=row[5],
            )
            for row in rows
        )

    def get_session(self, session_id: UUID) -> SessionDetail:
        with self._connect() as connection:
            row = connection.execute(
                sql.SQL("SELECT session_id, created_at FROM {} WHERE session_id = %s").format(
                    self._table("sewer_sessions")
                ),
                (session_id,),
            ).fetchone()
            if row is None:
                raise MissingRecord
            task_rows = connection.execute(
                sql.SQL(
                    "SELECT record FROM {} WHERE session_id = %s ORDER BY created_at, request_id"
                ).format(self._table("sewer_tasks")),
                (session_id,),
            ).fetchall()
        session = SessionRecord(session_id=row[0], created_at=row[1])
        tasks = tuple(TaskRecord.model_validate(task_row[0]) for task_row in task_rows)
        return SessionDetail(session=session, tasks=tasks)

    def _save_asset(self, connection: psycopg.Connection, asset: AssetRecord) -> None:
        parent = asset.parent
        values = (
            asset.ref.asset_id,
            asset.ref.version,
            parent.asset_id if parent else None,
            parent.version if parent else None,
            Jsonb(asset.model_dump(mode="json")),
        )
        inserted = connection.execute(
            sql.SQL(
                """
                INSERT INTO {} (
                    asset_id, version, parent_asset_id, parent_version, record
                ) VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (asset_id, version) DO NOTHING
                RETURNING record
                """
            ).format(self._table("sewer_assets")),
            values,
        ).fetchone()
        if inserted is not None:
            return
        existing = connection.execute(
            sql.SQL("SELECT record FROM {} WHERE asset_id = %s AND version = %s").format(
                self._table("sewer_assets")
            ),
            (asset.ref.asset_id, asset.ref.version),
        ).fetchone()
        if existing is None or AssetRecord.model_validate(existing[0]) != asset:
            raise SubmissionConflict

    def save_asset(self, asset: AssetRecord) -> None:
        try:
            with self._connect() as connection:
                self._save_asset(connection, asset)
        except psycopg.errors.ForeignKeyViolation as error:
            raise MissingRecord from error

    def get_asset(self, ref: AssetRef) -> AssetRecord:
        with self._connect() as connection:
            row = connection.execute(
                sql.SQL("SELECT record FROM {} WHERE asset_id = %s AND version = %s").format(
                    self._table("sewer_assets")
                ),
                (ref.asset_id, ref.version),
            ).fetchone()
        if row is None:
            raise MissingRecord
        return AssetRecord.model_validate(row[0])

    def find_task_by_request_id(self, request_id: UUID) -> TaskRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                sql.SQL("SELECT record FROM {} WHERE request_id = %s").format(
                    self._table("sewer_tasks")
                ),
                (request_id,),
            ).fetchone()
        return None if row is None else TaskRecord.model_validate(row[0])

    def create_task(self, submission: TaskSubmission) -> TaskRecord:
        created_at = datetime.now(timezone.utc)
        snapshot = TaskSnapshot(task_id=uuid4(), updated_at=created_at)
        candidate = TaskRecord(**submission.model_dump(), created_at=created_at, snapshot=snapshot)
        try:
            with self._connect() as connection:
                existing_row = connection.execute(
                    sql.SQL("SELECT submission, record FROM {} WHERE request_id = %s").format(
                        self._table("sewer_tasks")
                    ),
                    (submission.request_id,),
                ).fetchone()
                if existing_row is not None:
                    existing_submission = TaskSubmission.model_validate(existing_row[0])
                    if existing_submission != submission:
                        raise SubmissionConflict
                    return TaskRecord.model_validate(existing_row[1])

                session_exists = connection.execute(
                    sql.SQL("SELECT 1 FROM {} WHERE session_id = %s").format(
                        self._table("sewer_sessions")
                    ),
                    (submission.session_id,),
                ).fetchone()
                if session_exists is None:
                    raise MissingRecord
                asset_row = connection.execute(
                    sql.SQL("SELECT record FROM {} WHERE asset_id = %s AND version = %s").format(
                        self._table("sewer_assets")
                    ),
                    (submission.input_asset.asset_id, submission.input_asset.version),
                ).fetchone()
                if asset_row is None:
                    raise MissingRecord
                asset = AssetRecord.model_validate(asset_row[0])
                if asset.kind not in {AssetKind.IMAGE, AssetKind.VIDEO} or asset.parent is not None:
                    raise SubmissionConflict
                if submission.inspection_mode == "yolo_only" and asset.kind is AssetKind.VIDEO:
                    raise SubmissionConflict

                inserted = connection.execute(
                    sql.SQL(
                        """
                        INSERT INTO {} (
                            request_id, task_id, session_id,
                            input_asset_id, input_asset_version,
                            created_at, submission, record
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (request_id) DO NOTHING
                        RETURNING record
                        """
                    ).format(self._table("sewer_tasks")),
                    (
                        submission.request_id,
                        snapshot.task_id,
                        submission.session_id,
                        submission.input_asset.asset_id,
                        submission.input_asset.version,
                        created_at,
                        Jsonb(submission.model_dump(mode="json")),
                        Jsonb(candidate.model_dump(mode="json")),
                    ),
                ).fetchone()
                if inserted is not None:
                    return TaskRecord.model_validate(inserted[0])
                existing_row = connection.execute(
                    sql.SQL("SELECT submission, record FROM {} WHERE request_id = %s").format(
                        self._table("sewer_tasks")
                    ),
                    (submission.request_id,),
                ).fetchone()
                if existing_row is None:
                    raise MissingRecord
                existing_submission = TaskSubmission.model_validate(existing_row[0])
                if existing_submission != submission:
                    raise SubmissionConflict
                return TaskRecord.model_validate(existing_row[1])
        except psycopg.errors.ForeignKeyViolation as error:
            raise MissingRecord from error

    def get_task(self, task_id: UUID) -> TaskRecord:
        with self._connect() as connection:
            row = connection.execute(
                sql.SQL("SELECT record FROM {} WHERE task_id = %s").format(
                    self._table("sewer_tasks")
                ),
                (task_id,),
            ).fetchone()
        if row is None:
            raise MissingRecord
        return TaskRecord.model_validate(row[0])
