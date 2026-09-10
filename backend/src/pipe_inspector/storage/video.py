"""Atomic PostgreSQL checkpoints for the initial video scan."""

from datetime import datetime, timezone
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from pipe_inspector.contracts.assets import AssetKind, AssetRecord
from pipe_inspector.contracts.intake import TaskRecord
from pipe_inspector.contracts.tasks import TaskSnapshot
from pipe_inspector.contracts.video import (
    FrameObservation,
    VideoAggregationState,
    VideoScanResult,
)
from pipe_inspector.storage.repository import MissingRecord, SubmissionConflict
from pipe_inspector.storage.supplements import SupplementRepository
from pipe_inspector.vision.video_events import aggregate_chunk


class VideoRepository(SupplementRepository):
    """Persist one immutable frame prefix and its deterministic aggregate head."""

    def initialize(self) -> None:
        super().initialize()
        tasks = self._table("sewer_tasks")
        states = self._table("sewer_video_states")
        chunks = self._table("sewer_video_chunks")
        with self._connect() as connection:
            connection.execute(
                sql.SQL(
                    """
                    CREATE TABLE IF NOT EXISTS {} (
                        task_id uuid PRIMARY KEY REFERENCES {} (task_id),
                        state jsonb NOT NULL
                    )
                    """
                ).format(states, tasks)
            )
            connection.execute(
                sql.SQL(
                    """
                    CREATE TABLE IF NOT EXISTS {} (
                        task_id uuid NOT NULL REFERENCES {} (task_id),
                        start_frame integer NOT NULL CHECK (start_frame >= 0),
                        end_frame integer NOT NULL CHECK (end_frame >= start_frame),
                        attempt integer NOT NULL CHECK (attempt >= 1),
                        observations jsonb NOT NULL,
                        PRIMARY KEY (task_id, start_frame),
                        UNIQUE (task_id, end_frame)
                    )
                    """
                ).format(chunks, tasks)
            )

    @staticmethod
    def _is_empty_state(state: VideoAggregationState) -> bool:
        return (
            state.next_frame == 0
            and state.last_timestamp_seconds is None
            and state.adequate_frames == 0
            and not state.active
            and not state.events
            and not state.audit_frames
            and not state.finalized
        )

    @staticmethod
    def _same_initial_pins(stored: VideoAggregationState, candidate: VideoAggregationState) -> bool:
        if stored.source != candidate.source or stored.config != candidate.config:
            return False
        for key in ("detector_id", "model_version", "label_space_version"):
            requested = getattr(candidate, key)
            if requested is not None and requested != getattr(stored, key):
                return False
        return True

    def ensure_video_state(
        self, task_id: UUID, state: VideoAggregationState
    ) -> VideoAggregationState:
        if not self._is_empty_state(state):
            raise SubmissionConflict
        try:
            with self._connect() as connection:
                task_row = connection.execute(
                    sql.SQL("SELECT record FROM {} WHERE task_id = %s FOR UPDATE").format(
                        self._table("sewer_tasks")
                    ),
                    (task_id,),
                ).fetchone()
                if task_row is None:
                    raise MissingRecord
                task = TaskRecord.model_validate(task_row[0])
                if task.input_asset != state.source or task.parent_task_id is not None:
                    raise SubmissionConflict
                asset_row = connection.execute(
                    sql.SQL("SELECT record FROM {} WHERE asset_id = %s AND version = %s").format(
                        self._table("sewer_assets")
                    ),
                    (task.input_asset.asset_id, task.input_asset.version),
                ).fetchone()
                if asset_row is None:
                    raise MissingRecord
                asset = AssetRecord.model_validate(asset_row[0])
                if asset.kind is not AssetKind.VIDEO or asset.parent is not None:
                    raise SubmissionConflict

                existing = connection.execute(
                    sql.SQL("SELECT state FROM {} WHERE task_id = %s").format(
                        self._table("sewer_video_states")
                    ),
                    (task_id,),
                ).fetchone()
                if existing is not None:
                    stored = VideoAggregationState.model_validate(existing[0])
                    if not self._same_initial_pins(stored, state):
                        raise SubmissionConflict
                    return stored
                if task.snapshot.is_terminal:
                    raise SubmissionConflict

                row = connection.execute(
                    sql.SQL(
                        """
                        INSERT INTO {} (task_id, state)
                        VALUES (%s, %s)
                        ON CONFLICT (task_id) DO NOTHING
                        RETURNING state
                        """
                    ).format(self._table("sewer_video_states")),
                    (task_id, Jsonb(state.model_dump(mode="json"))),
                ).fetchone()
                if row is None:
                    row = connection.execute(
                        sql.SQL("SELECT state FROM {} WHERE task_id = %s").format(
                            self._table("sewer_video_states")
                        ),
                        (task_id,),
                    ).fetchone()
                if row is None:
                    raise MissingRecord
                stored = VideoAggregationState.model_validate(row[0])
                if not self._same_initial_pins(stored, state):
                    raise SubmissionConflict
                return stored
        except psycopg.errors.ForeignKeyViolation as error:
            raise MissingRecord from error

    def load_video_state(self, task_id: UUID) -> VideoAggregationState | None:
        with self._connect() as connection:
            row = connection.execute(
                sql.SQL("SELECT state FROM {} WHERE task_id = %s").format(
                    self._table("sewer_video_states")
                ),
                (task_id,),
            ).fetchone()
        return None if row is None else VideoAggregationState.model_validate(row[0])

    def _validate_replayed_prefix(
        self,
        connection: psycopg.Connection,
        task_id: UUID,
        observations: tuple[FrameObservation, ...],
    ) -> None:
        rows = connection.execute(
            sql.SQL(
                """
                SELECT observations FROM {}
                WHERE task_id = %s
                  AND end_frame >= %s
                  AND start_frame <= %s
                ORDER BY start_frame
                """
            ).format(self._table("sewer_video_chunks")),
            (task_id, observations[0].frame_index, observations[-1].frame_index),
        ).fetchall()
        saved = {
            frame.frame_index: frame
            for row in rows
            for frame in (FrameObservation.model_validate(item) for item in row[0])
        }
        if any(saved.get(frame.frame_index) != frame for frame in observations):
            raise SubmissionConflict

    def _first_timestamp(
        self,
        connection: psycopg.Connection,
        task_id: UUID,
        observations: tuple[FrameObservation, ...],
    ) -> float:
        row = connection.execute(
            sql.SQL(
                """
                SELECT observations FROM {}
                WHERE task_id = %s
                ORDER BY start_frame
                LIMIT 1
                """
            ).format(self._table("sewer_video_chunks")),
            (task_id,),
        ).fetchone()
        if row is None:
            return observations[0].timestamp_seconds
        return FrameObservation.model_validate(row[0][0]).timestamp_seconds

    def commit_video_chunk(
        self,
        task_id: UUID,
        observations: tuple[FrameObservation, ...],
        *,
        attempt: int = 1,
    ) -> VideoAggregationState:
        if not observations:
            raise ValueError("video chunk must contain observations")
        if attempt < 1:
            raise ValueError("attempt must be at least one")
        try:
            with self._connect() as connection:
                task_row = connection.execute(
                    sql.SQL("SELECT record FROM {} WHERE task_id = %s FOR UPDATE").format(
                        self._table("sewer_tasks")
                    ),
                    (task_id,),
                ).fetchone()
                if task_row is None:
                    raise MissingRecord
                task = TaskRecord.model_validate(task_row[0])
                state_row = connection.execute(
                    sql.SQL("SELECT state FROM {} WHERE task_id = %s FOR UPDATE").format(
                        self._table("sewer_video_states")
                    ),
                    (task_id,),
                ).fetchone()
                if state_row is None:
                    raise MissingRecord
                state = VideoAggregationState.model_validate(state_row[0])

                start_frame = observations[0].frame_index
                end_frame = observations[-1].frame_index
                if start_frame < state.next_frame:
                    if end_frame >= state.next_frame:
                        raise SubmissionConflict
                    self._validate_replayed_prefix(connection, task_id, observations)
                    return state
                if task.snapshot.is_terminal or state.finalized:
                    raise SubmissionConflict
                if start_frame != state.next_frame:
                    raise SubmissionConflict
                try:
                    updated_state = aggregate_chunk(state, observations)
                except ValueError as error:
                    raise SubmissionConflict from error

                first_timestamp = self._first_timestamp(connection, task_id, observations)
                asset_row = connection.execute(
                    sql.SQL("SELECT record FROM {} WHERE asset_id = %s AND version = %s").format(
                        self._table("sewer_assets")
                    ),
                    (task.input_asset.asset_id, task.input_asset.version),
                ).fetchone()
                if asset_row is None:
                    raise MissingRecord
                asset = AssetRecord.model_validate(asset_row[0])
                if asset.kind is not AssetKind.VIDEO or asset.duration_seconds is None:
                    raise SubmissionConflict
                elapsed = max(0.0, observations[-1].timestamp_seconds - first_timestamp)
                scan_progress = min(1.0, elapsed / asset.duration_seconds)
                overall_progress = min(0.79, 0.05 + 0.70 * scan_progress)
                snapshot = TaskSnapshot.model_validate(
                    task.snapshot.model_dump()
                    | {
                        "runtime_status": "running",
                        "stage": "detecting",
                        "stage_progress": scan_progress,
                        "overall_progress": overall_progress,
                        "processed_frames": updated_state.next_frame,
                        "processed_chunks": task.snapshot.processed_chunks + 1,
                        "active_tool": "detect",
                        "retry_count": max(task.snapshot.retry_count, attempt - 1),
                        "updated_at": datetime.now(timezone.utc),
                    }
                )
                updated_task = TaskRecord.model_validate(task.model_dump() | {"snapshot": snapshot})
                connection.execute(
                    sql.SQL(
                        """
                        INSERT INTO {} (
                            task_id, start_frame, end_frame, attempt, observations
                        ) VALUES (%s, %s, %s, %s, %s)
                        """
                    ).format(self._table("sewer_video_chunks")),
                    (
                        task_id,
                        start_frame,
                        end_frame,
                        attempt,
                        Jsonb([item.model_dump(mode="json") for item in observations]),
                    ),
                )
                connection.execute(
                    sql.SQL("UPDATE {} SET state = %s WHERE task_id = %s").format(
                        self._table("sewer_video_states")
                    ),
                    (Jsonb(updated_state.model_dump(mode="json")), task_id),
                )
                connection.execute(
                    sql.SQL("UPDATE {} SET record = %s WHERE task_id = %s").format(
                        self._table("sewer_tasks")
                    ),
                    (Jsonb(updated_task.model_dump(mode="json")), task_id),
                )
                return updated_state
        except psycopg.errors.ForeignKeyViolation as error:
            raise MissingRecord from error
        except psycopg.errors.UniqueViolation as error:
            raise SubmissionConflict from error

    def get_video_frame(self, task_id: UUID, frame_index: int) -> FrameObservation:
        if frame_index < 0:
            raise MissingRecord
        with self._connect() as connection:
            row = connection.execute(
                sql.SQL(
                    """
                    SELECT observations FROM {}
                    WHERE task_id = %s
                      AND start_frame <= %s
                      AND end_frame >= %s
                    """
                ).format(self._table("sewer_video_chunks")),
                (task_id, frame_index, frame_index),
            ).fetchone()
        if row is None:
            raise MissingRecord
        for raw in row[0]:
            observation = FrameObservation.model_validate(raw)
            if observation.frame_index == frame_index:
                return observation
        raise MissingRecord

    def finish_video_scan(self, task_id: UUID, *, error_code: str | None = None) -> VideoScanResult:
        with self._connect() as connection:
            task_row = connection.execute(
                sql.SQL("SELECT record FROM {} WHERE task_id = %s FOR UPDATE").format(
                    self._table("sewer_tasks")
                ),
                (task_id,),
            ).fetchone()
            if task_row is None:
                raise MissingRecord
            task = TaskRecord.model_validate(task_row[0])
            state_row = connection.execute(
                sql.SQL("SELECT state FROM {} WHERE task_id = %s FOR UPDATE").format(
                    self._table("sewer_video_states")
                ),
                (task_id,),
            ).fetchone()
            if state_row is None:
                raise MissingRecord
            cached = connection.execute(
                sql.SQL("SELECT payload FROM {} WHERE task_id = %s AND step = 'video_scan'").format(
                    self._table("sewer_step_checkpoints")
                ),
                (task_id,),
            ).fetchone()
            if cached is not None:
                return VideoScanResult.model_validate(cached[0])
            if task.snapshot.is_terminal:
                raise SubmissionConflict

            state = VideoAggregationState.model_validate(state_row[0])
            try:
                finalized = aggregate_chunk(state, (), final=True)
            except ValueError as error:
                raise SubmissionConflict from error
            result = VideoScanResult(
                state=finalized,
                decode_complete=error_code is None and finalized.next_frame > 0,
                error_code=error_code,
            )
            payload = result.model_dump(mode="json")
            connection.execute(
                sql.SQL("UPDATE {} SET state = %s WHERE task_id = %s").format(
                    self._table("sewer_video_states")
                ),
                (Jsonb(finalized.model_dump(mode="json")), task_id),
            )
            connection.execute(
                sql.SQL(
                    """
                    INSERT INTO {} (task_id, step, payload)
                    VALUES (%s, 'video_scan', %s)
                    """
                ).format(self._table("sewer_step_checkpoints")),
                (task_id, Jsonb(payload)),
            )
            return result
