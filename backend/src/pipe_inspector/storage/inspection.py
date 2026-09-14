"""Durable run pins, tool attempts, checkpoints, and task projections."""

import math
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from pipe_inspector.contracts.assets import AssetKind, AssetRecord, AssetRef
from pipe_inspector.contracts.enhancement import EnhancementKind, EnhancementResult
from pipe_inspector.contracts.inspection import ImageObservation, InspectionDecision
from pipe_inspector.contracts.intake import TaskRecord
from pipe_inspector.contracts.tasks import TaskSnapshot
from pipe_inspector.contracts.tools import ToolCall, ToolCallStatus
from pipe_inspector.contracts.video import FrameObservation, VideoFrameToolPin
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
                        sql.SQL("SELECT run_id, task_id, config FROM {} WHERE task_id = %s").format(
                            self._table("sewer_inspection_runs")
                        ),
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
        *,
        allow_error_checkpoint: bool = False,
    ) -> dict:
        if not step.strip():
            raise ValueError("step must not be empty")
        if call is not None:
            if call.task_id != task_id or call.step_id != step:
                raise ValueError("call must belong to the checkpoint task and step")
            allowed_statuses = {ToolCallStatus.SUCCEEDED, ToolCallStatus.SKIPPED}
            if allow_error_checkpoint:
                allowed_statuses.update({ToolCallStatus.FAILED, ToolCallStatus.UNCERTAIN})
            if call.status not in allowed_statuses:
                raise ValueError("checkpoint call status is not allowed")
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
                        sql.SQL("SELECT payload FROM {} WHERE task_id = %s AND step = %s").format(
                            self._table("sewer_step_checkpoints")
                        ),
                        (task_id, step),
                    ).fetchone()
                if row is None:
                    raise MissingRecord
                return row[0]
        except psycopg.errors.ForeignKeyViolation as error:
            raise MissingRecord from error

    def save_enhancement(
        self,
        task_id: UUID,
        result: EnhancementResult,
        call: ToolCall,
    ) -> dict:
        payload = result.model_dump(mode="json")
        try:
            with self._connect() as connection:
                task_row = connection.execute(
                    sql.SQL(
                        """
                        SELECT input_asset_id, input_asset_version, record
                        FROM {} WHERE task_id = %s FOR UPDATE
                        """
                    ).format(self._table("sewer_tasks")),
                    (task_id,),
                ).fetchone()
                if task_row is None:
                    raise MissingRecord
                checkpoint = connection.execute(
                    sql.SQL(
                        "SELECT payload FROM {} WHERE task_id = %s AND step = 'enhance'"
                    ).format(self._table("sewer_step_checkpoints")),
                    (task_id,),
                ).fetchone()
                run_row = connection.execute(
                    sql.SQL("SELECT run_id, config FROM {} WHERE task_id = %s").format(
                        self._table("sewer_inspection_runs")
                    ),
                    (task_id,),
                ).fetchone()
                if run_row is None:
                    raise MissingRecord
                config = run_row[1]
                if "auto_enhancement" in config:
                    from pipe_inspector.workflow.enhancement_config import selected_enhancement_pin

                    selection = connection.execute(
                        sql.SQL(
                            "SELECT payload FROM {} WHERE task_id = %s "
                            "AND step = 'enhancement_selection'"
                        ).format(self._table("sewer_step_checkpoints")),
                        (task_id,),
                    ).fetchone()
                    try:
                        pin = selected_enhancement_pin(config, selection[0]["selection"])
                        if pin is None:
                            raise ValueError("no selected enhancement")
                    except (ValueError, KeyError, TypeError):
                        raise SubmissionConflict from None
                else:
                    pin = self._enhancement_pin(config)
                self._validate_enhancement_call_identity(task_id, run_row[0], pin, call)
                if checkpoint is not None:
                    return EnhancementResult.model_validate(checkpoint[0]).model_dump(mode="json")

                input_ref = AssetRef(asset_id=task_row[0], version=task_row[1])
                input_row = connection.execute(
                    sql.SQL("SELECT record FROM {} WHERE asset_id = %s AND version = %s").format(
                        self._table("sewer_assets")
                    ),
                    (input_ref.asset_id, input_ref.version),
                ).fetchone()
                if input_row is None:
                    raise MissingRecord
                input_asset = AssetRecord.model_validate(input_row[0])
                task = TaskRecord.model_validate(task_row[2])
                allow_frame = self._validate_frame_tool_source(
                    connection, task, input_asset, config, operation="enhance"
                )
                self._validate_enhancement_first_write(
                    input_asset, result, call, pin, allow_frame=allow_frame
                )
                existing_output = connection.execute(
                    sql.SQL("SELECT 1 FROM {} WHERE asset_id = %s AND version = %s").format(
                        self._table("sewer_assets")
                    ),
                    (result.output_asset.ref.asset_id, result.output_asset.ref.version),
                ).fetchone()
                if existing_output is not None:
                    raise SubmissionConflict
                self._save_asset(connection, result.output_asset)
                self._record_call(connection, call)
                connection.execute(
                    sql.SQL(
                        "INSERT INTO {} (task_id, step, payload) VALUES (%s, 'enhance', %s)"
                    ).format(self._table("sewer_step_checkpoints")),
                    (task_id, Jsonb(payload)),
                )
                return payload
        except psycopg.errors.ForeignKeyViolation as error:
            raise MissingRecord from error

    def save_geometry(self, task_id, step, result, call, measurements=None):
        from pipe_inspector.storage.geometry import save_geometry

        return save_geometry(self, task_id, step, result, call, measurements)

    @staticmethod
    def _enhancement_pin(config: dict) -> dict:
        pin = config.get("enhancement") if isinstance(config, dict) else None
        if not isinstance(pin, dict):
            raise SubmissionConflict
        return pin

    @staticmethod
    def _validate_enhancement_call_identity(
        task_id: UUID,
        run_id: UUID,
        pin: dict,
        call: ToolCall,
    ) -> None:
        if (
            call.task_id != task_id
            or call.step_id != "enhance"
            or call.idempotency_key != f"{run_id}:enhance"
            or str(call.call_id) != pin.get("call_id")
        ):
            raise SubmissionConflict

    @staticmethod
    def _enhancement_source_box(
        input_asset: AssetRecord,
        result: EnhancementResult,
        options: object,
    ) -> tuple[int, int, int, int]:
        if not isinstance(options, dict) or set(options) != {"kind", "roi_xyxy"}:
            raise SubmissionConflict
        if options["kind"] != result.kind:
            raise SubmissionConflict
        roi = options["roi_xyxy"]
        if result.kind == EnhancementKind.SUPER_RESOLUTION and roi is None:
            raise SubmissionConflict
        if roi is None:
            box = (0, 0, input_asset.width, input_asset.height)
        else:
            if (
                not isinstance(roi, (list, tuple))
                or len(roi) != 4
                or any(
                    isinstance(value, bool) or not isinstance(value, (int, float)) for value in roi
                )
                or any(not math.isfinite(value) for value in roi)
            ):
                raise SubmissionConflict
            x1, y1, x2, y2 = roi
            if not (0 <= x1 < x2 <= input_asset.width and 0 <= y1 < y2 <= input_asset.height):
                raise SubmissionConflict
            box = (math.floor(x1), math.floor(y1), math.ceil(x2), math.ceil(y2))
        if (
            result.source_region.width != input_asset.width
            or result.source_region.height != input_asset.height
            or result.source_region.bbox_xyxy != box
        ):
            raise SubmissionConflict
        return box

    @classmethod
    def _validate_enhancement_first_write(
        cls,
        input_asset: AssetRecord,
        result: EnhancementResult,
        call: ToolCall,
        pin: dict,
        *,
        allow_frame: bool = False,
    ) -> None:
        valid_input = (input_asset.kind is AssetKind.IMAGE and input_asset.parent is None) or (
            allow_frame and input_asset.kind is AssetKind.FRAME and input_asset.parent is not None
        )
        if (
            not valid_input
            or result.input_asset != input_asset.ref
            or result.output_asset.kind != AssetKind.ENHANCED_IMAGE
            or result.output_asset.parent != input_asset.ref
            or result.output_asset.ref == input_asset.ref
        ):
            raise SubmissionConflict
        if (
            pin.get("version") != "enhancement-online-v1"
            or pin.get("model_id") != result.model_id
            or pin.get("model_version") != result.model_version
        ):
            raise SubmissionConflict
        box = cls._enhancement_source_box(input_asset, result, pin.get("options"))
        scale = 4 if result.kind == EnhancementKind.SUPER_RESOLUTION else 1
        expected_size = ((box[2] - box[0]) * scale, (box[3] - box[1]) * scale)
        if (result.output_asset.width, result.output_asset.height) != expected_size:
            raise SubmissionConflict
        if (
            call.status != ToolCallStatus.SUCCEEDED
            or call.tool_name != "enhance"
            or call.resource != "gpu"
            or call.input_assets != (input_asset.ref,)
            or call.output_assets != (result.output_asset.ref,)
            or call.model_version != result.model_version
        ):
            raise SubmissionConflict

    def _validate_frame_tool_source(
        self,
        connection: psycopg.Connection,
        task: TaskRecord,
        asset: AssetRecord,
        config: dict,
        *,
        operation: str,
    ) -> bool:
        if asset.kind is AssetKind.IMAGE and asset.parent is None:
            if "frame_source" in config or task.frame_source is not None:
                raise SubmissionConflict
            return False
        if asset.kind is not AssetKind.FRAME or asset.parent is None:
            raise SubmissionConflict
        try:
            pin = VideoFrameToolPin.model_validate(config["frame_source"])
            checkpoint_row = connection.execute(
                sql.SQL("SELECT payload FROM {} WHERE task_id=%s AND step='frame_source'").format(
                    self._table("sewer_step_checkpoints")
                ),
                (task.snapshot.task_id,),
            ).fetchone()
            source_row = connection.execute(
                sql.SQL("SELECT payload FROM {} WHERE task_id=%s AND step='source'").format(
                    self._table("sewer_step_checkpoints")
                ),
                (task.snapshot.task_id,),
            ).fetchone()
            detect_row = connection.execute(
                sql.SQL("SELECT payload FROM {} WHERE task_id=%s AND step='detect'").format(
                    self._table("sewer_step_checkpoints")
                ),
                (task.snapshot.task_id,),
            ).fetchone()
            checkpoint = checkpoint_row[0]
            observation = FrameObservation.model_validate(checkpoint["observation"])
            decision = InspectionDecision.model_validate(checkpoint["decision"])
            parent = TaskRecord.model_validate(source_row[0]["task"])
            detected = ImageObservation.model_validate(detect_row[0])
        except (KeyError, TypeError, ValueError):
            raise SubmissionConflict from None
        frame_source = task.frame_source
        expected = ImageObservation(detection=observation.detection, quality=observation.quality)
        if (
            VideoFrameToolPin.model_validate(checkpoint["pin"]) != pin
            or pin.operation != operation
            or task.parent_task_id != pin.parent_task_id
            or task.input_asset != pin.frame_asset
            or frame_source is None
            or frame_source.video_asset != pin.video_asset
            or frame_source.frame_index != pin.frame_index
            or frame_source.timestamp_seconds != pin.timestamp_seconds
            or asset.ref != pin.frame_asset
            or asset.parent != pin.video_asset
            or (asset.width, asset.height) != (pin.width, pin.height)
            or parent.snapshot.task_id != pin.parent_task_id
            or parent.input_asset != pin.video_asset
            or parent.session_id != task.session_id
            or observation.frame_index != pin.frame_index
            or observation.timestamp_seconds != pin.timestamp_seconds
            or (observation.detection.width, observation.detection.height)
            != (pin.width, pin.height)
            or detected != expected
            or any(
                evidence.asset != pin.frame_asset
                or evidence.frame_index != pin.frame_index
                or evidence.timestamp_seconds != pin.timestamp_seconds
                for finding in decision.findings
                for evidence in finding.evidence
            )
        ):
            raise SubmissionConflict
        return True

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
                    "SELECT record FROM {} WHERE task_id = %s ORDER BY step_id, call_id, attempt"
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
            updated = TaskRecord.model_validate(current.model_dump() | {"snapshot": snapshot})
            connection.execute(
                sql.SQL("UPDATE {} SET record = %s WHERE task_id = %s").format(
                    self._table("sewer_tasks")
                ),
                (Jsonb(updated.model_dump(mode="json")), snapshot.task_id),
            )
        return updated
