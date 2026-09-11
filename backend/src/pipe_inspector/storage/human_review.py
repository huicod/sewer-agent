"""Atomic task projection and immutable human-review audit persistence."""

from datetime import datetime, timezone
from uuid import UUID

from psycopg import sql
from psycopg.types.json import Jsonb

from pipe_inspector.agent.human_review import resolve_human_review
from pipe_inspector.contracts.human_review import HumanReviewRecord, HumanReviewSubmission
from pipe_inspector.contracts.intake import TaskRecord
from pipe_inspector.storage.repository import MissingRecord, SubmissionConflict
from pipe_inspector.storage.video import VideoRepository


class HumanReviewRepository(VideoRepository):
    """Persist exactly one complete human-review batch per task."""

    def initialize(self) -> None:
        super().initialize()
        with self._connect() as connection:
            connection.execute(
                sql.SQL(
                    """
                    CREATE TABLE IF NOT EXISTS {} (
                        task_id uuid PRIMARY KEY REFERENCES {} (task_id),
                        request_id uuid UNIQUE NOT NULL,
                        submission jsonb NOT NULL,
                        before_snapshot jsonb NOT NULL,
                        after_snapshot jsonb NOT NULL,
                        reviewed_at timestamptz NOT NULL
                    )
                    """
                ).format(
                    self._table("sewer_human_reviews"),
                    self._table("sewer_tasks"),
                )
            )

    @staticmethod
    def _record(row: tuple) -> HumanReviewRecord:
        return HumanReviewRecord(
            task_id=row[0],
            submission=row[2],
            before=row[3],
            after=row[4],
            reviewed_at=row[5],
        )

    def get_human_review(self, task_id: UUID) -> HumanReviewRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                sql.SQL(
                    """
                    SELECT task_id, request_id, submission,
                           before_snapshot, after_snapshot, reviewed_at
                    FROM {} WHERE task_id = %s
                    """
                ).format(self._table("sewer_human_reviews")),
                (task_id,),
            ).fetchone()
        return None if row is None else self._record(row)

    def apply_human_review(
        self, task_id: UUID, submission: HumanReviewSubmission
    ) -> HumanReviewRecord:
        reviews = self._table("sewer_human_reviews")
        tasks = self._table("sewer_tasks")
        with self._connect() as connection:
            task_row = connection.execute(
                sql.SQL("SELECT record FROM {} WHERE task_id = %s FOR UPDATE").format(tasks),
                (task_id,),
            ).fetchone()
            if task_row is None:
                raise MissingRecord

            request_row = connection.execute(
                sql.SQL(
                    """
                    SELECT task_id, request_id, submission,
                           before_snapshot, after_snapshot, reviewed_at
                    FROM {} WHERE request_id = %s
                    """
                ).format(reviews),
                (submission.request_id,),
            ).fetchone()
            if request_row is not None:
                record = self._record(request_row)
                if record.task_id != task_id or record.submission != submission:
                    raise SubmissionConflict
                return record

            existing_row = connection.execute(
                sql.SQL(
                    """
                    SELECT task_id, request_id, submission,
                           before_snapshot, after_snapshot, reviewed_at
                    FROM {} WHERE task_id = %s
                    """
                ).format(reviews),
                (task_id,),
            ).fetchone()
            if existing_row is not None:
                raise SubmissionConflict

            task = TaskRecord.model_validate(task_row[0])
            reviewed_at = datetime.now(timezone.utc)
            after = resolve_human_review(task.snapshot, submission, now=reviewed_at)
            record = HumanReviewRecord(
                task_id=task_id,
                submission=submission,
                before=task.snapshot,
                after=after,
                reviewed_at=reviewed_at,
            )
            inserted = connection.execute(
                sql.SQL(
                    """
                    INSERT INTO {} (
                        task_id, request_id, submission,
                        before_snapshot, after_snapshot, reviewed_at
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    RETURNING task_id
                    """
                ).format(reviews),
                (
                    task_id,
                    submission.request_id,
                    Jsonb(submission.model_dump(mode="json")),
                    Jsonb(task.snapshot.model_dump(mode="json")),
                    Jsonb(after.model_dump(mode="json")),
                    reviewed_at,
                ),
            ).fetchone()
            if inserted is None:
                raise SubmissionConflict

            updated_task = TaskRecord.model_validate(task.model_dump() | {"snapshot": after})
            connection.execute(
                sql.SQL("UPDATE {} SET record = %s WHERE task_id = %s").format(tasks),
                (Jsonb(updated_task.model_dump(mode="json")), task_id),
            )
        return record
