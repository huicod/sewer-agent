"""Atomic admission for one linked image re-review per original task."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from pipe_inspector.agent.supplement_policy import SUPPLEMENT_POLICY_VERSION
from pipe_inspector.contracts.assets import AssetKind, AssetRecord
from pipe_inspector.contracts.inspection import ImageObservation
from pipe_inspector.contracts.intake import TaskRecord, TaskSubmission
from pipe_inspector.contracts.tasks import RuntimeStatus, TaskSnapshot
from pipe_inspector.storage.conversation import ConversationRepository
from pipe_inspector.storage.repository import MissingRecord, SubmissionConflict


class SupplementNotAllowed(RuntimeError):
    """A safe, stable reason why a source task cannot be supplemented."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class SupplementRepository(ConversationRepository):
    def initialize(self) -> None:
        super().initialize()
        with self._connect() as connection:
            connection.execute(
                sql.SQL(
                    """
                    CREATE TABLE IF NOT EXISTS {} (
                        parent_task_id uuid PRIMARY KEY REFERENCES {} (task_id),
                        task_id uuid UNIQUE NOT NULL REFERENCES {} (task_id)
                    )
                    """
                ).format(
                    self._table("sewer_image_supplements"),
                    self._table("sewer_tasks"),
                    self._table("sewer_tasks"),
                )
            )

    def _existing_request(
        self,
        connection: psycopg.Connection,
        parent_task_id: UUID,
        submission: TaskSubmission,
    ) -> TaskRecord | None:
        row = connection.execute(
            sql.SQL(
                """
                SELECT tasks.submission, tasks.record, supplements.parent_task_id
                FROM {} AS tasks
                LEFT JOIN {} AS supplements ON supplements.task_id = tasks.task_id
                WHERE tasks.request_id = %s
                """
            ).format(
                self._table("sewer_tasks"),
                self._table("sewer_image_supplements"),
            ),
            (submission.request_id,),
        ).fetchone()
        if row is None:
            return None
        if TaskSubmission.model_validate(row[0]) != submission or row[2] != parent_task_id:
            raise SubmissionConflict
        return TaskRecord.model_validate(row[1])

    @staticmethod
    def _validate_config(
        config: dict,
        parent_task_id: UUID,
        parent_run: dict,
        observation: ImageObservation,
    ) -> None:
        required = {
            "operation": "supplement_review",
            "parent_task_id": str(parent_task_id),
            "force_review": True,
            "supplement_policy_version": SUPPLEMENT_POLICY_VERSION,
        }
        if any(config.get(key) != value for key, value in required.items()):
            raise SubmissionConflict
        detection = observation.detection
        for key in ("detector_id", "model_version", "label_space_version"):
            observed = getattr(detection, key)
            if parent_run.get(key) != observed:
                raise SupplementNotAllowed("source_evidence_missing")
            if config.get(key) != observed:
                raise SubmissionConflict

    def admit_supplement(
        self,
        parent_task_id: UUID,
        submission: TaskSubmission,
        config: dict,
    ) -> TaskRecord:
        created_at = datetime.now(timezone.utc)
        child_id = uuid4()
        candidate = TaskRecord(
            **submission.model_dump(),
            created_at=created_at,
            snapshot=TaskSnapshot(task_id=child_id, updated_at=created_at),
            parent_task_id=parent_task_id,
        )
        try:
            with self._connect() as connection:
                existing = self._existing_request(connection, parent_task_id, submission)
                if existing is not None:
                    return existing

                parent_row = connection.execute(
                    sql.SQL("SELECT record FROM {} WHERE task_id = %s FOR UPDATE").format(
                        self._table("sewer_tasks")
                    ),
                    (parent_task_id,),
                ).fetchone()
                if parent_row is None:
                    raise MissingRecord
                existing = self._existing_request(connection, parent_task_id, submission)
                if existing is not None:
                    return existing
                parent = TaskRecord.model_validate(parent_row[0])
                if parent.session_id != submission.session_id:
                    raise SubmissionConflict
                if (
                    parent.inspection_mode == "yolo_only"
                    or submission.inspection_mode != parent.inspection_mode
                ):
                    raise SubmissionConflict
                if parent.input_asset != submission.input_asset:
                    raise SubmissionConflict
                if parent.parent_task_id is not None:
                    raise SupplementNotAllowed("supplement_limit")
                if parent.snapshot.runtime_status not in {
                    RuntimeStatus.COMPLETED,
                    RuntimeStatus.PARTIAL,
                }:
                    raise SupplementNotAllowed("source_not_ready")

                asset_row = connection.execute(
                    sql.SQL("SELECT record FROM {} WHERE asset_id = %s AND version = %s").format(
                        self._table("sewer_assets")
                    ),
                    (parent.input_asset.asset_id, parent.input_asset.version),
                ).fetchone()
                if asset_row is None:
                    raise MissingRecord
                asset = AssetRecord.model_validate(asset_row[0])
                if asset.kind != AssetKind.IMAGE or asset.parent is not None:
                    raise SupplementNotAllowed("image_only")

                used = connection.execute(
                    sql.SQL("SELECT 1 FROM {} WHERE parent_task_id = %s").format(
                        self._table("sewer_image_supplements")
                    ),
                    (parent_task_id,),
                ).fetchone()
                if used is not None:
                    raise SupplementNotAllowed("supplement_limit")

                run_row = connection.execute(
                    sql.SQL("SELECT config FROM {} WHERE task_id = %s").format(
                        self._table("sewer_inspection_runs")
                    ),
                    (parent_task_id,),
                ).fetchone()
                detect_row = connection.execute(
                    sql.SQL("SELECT payload FROM {} WHERE task_id = %s AND step = 'detect'").format(
                        self._table("sewer_step_checkpoints")
                    ),
                    (parent_task_id,),
                ).fetchone()
                if run_row is None or detect_row is None:
                    raise SupplementNotAllowed("source_evidence_missing")
                try:
                    observation = ImageObservation.model_validate(detect_row[0])
                except ValueError:
                    raise SupplementNotAllowed("source_evidence_missing") from None
                self._validate_config(config, parent_task_id, run_row[0], observation)

                verify_row = connection.execute(
                    sql.SQL("SELECT payload FROM {} WHERE task_id = %s AND step = 'verify'").format(
                        self._table("sewer_step_checkpoints")
                    ),
                    (parent_task_id,),
                ).fetchone()
                connection.execute(
                    sql.SQL(
                        """
                        INSERT INTO {} (
                            request_id, task_id, session_id,
                            input_asset_id, input_asset_version,
                            created_at, submission, record
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """
                    ).format(self._table("sewer_tasks")),
                    (
                        submission.request_id,
                        child_id,
                        submission.session_id,
                        submission.input_asset.asset_id,
                        submission.input_asset.version,
                        created_at,
                        Jsonb(submission.model_dump(mode="json")),
                        Jsonb(candidate.model_dump(mode="json")),
                    ),
                )
                connection.execute(
                    sql.SQL("INSERT INTO {} (parent_task_id, task_id) VALUES (%s, %s)").format(
                        self._table("sewer_image_supplements")
                    ),
                    (parent_task_id, child_id),
                )
                connection.execute(
                    sql.SQL("INSERT INTO {} (task_id, run_id, config) VALUES (%s, %s, %s)").format(
                        self._table("sewer_inspection_runs")
                    ),
                    (child_id, uuid4(), Jsonb(config)),
                )
                checkpoints = (
                    ("detect", detect_row[0]),
                    (
                        "source",
                        {
                            "task": parent.model_dump(mode="json"),
                            "verify": None if verify_row is None else verify_row[0],
                        },
                    ),
                )
                checkpoint_insert = sql.SQL(
                    "INSERT INTO {} (task_id, step, payload) VALUES (%s, %s, %s)"
                ).format(self._table("sewer_step_checkpoints"))
                for step, payload in checkpoints:
                    connection.execute(checkpoint_insert, (child_id, step, Jsonb(payload)))
        except psycopg.errors.ForeignKeyViolation as error:
            raise MissingRecord from error
        except psycopg.errors.UniqueViolation as error:
            raise SubmissionConflict from error
        return candidate
