"""Atomic validation and persistence for local geometry evidence."""

from uuid import UUID

from psycopg import sql
from psycopg.types.json import Jsonb

from pipe_inspector.contracts.assets import AssetKind, AssetRecord, AssetRef
from pipe_inspector.contracts.geometry import (
    GeometryMeasurements,
    GeometryOptions,
    RelativeDepthResult,
    SegmentationResult,
)
from pipe_inspector.contracts.inspection import InspectionDecision
from pipe_inspector.contracts.tools import ToolCall, ToolCallStatus
from pipe_inspector.storage.repository import MissingRecord, SubmissionConflict


def _result_assets(result) -> tuple[AssetRecord, AssetRecord]:
    if isinstance(result, SegmentationResult):
        return result.mask_asset, result.overlay_asset
    if isinstance(result, RelativeDepthResult):
        return result.raw_asset, result.preview_asset
    raise SubmissionConflict


def _validate_call(
    task_id: UUID,
    run_id: UUID,
    step: str,
    pin: dict,
    operation: str,
    result,
    call: ToolCall,
) -> None:
    assets = _result_assets(result)
    expected_tool = (
        "segment"
        if step == "geometry_segment"
        else "relative_measure"
        if operation == "relative_measure"
        else "relative_depth"
    )
    if (
        call.task_id != task_id
        or call.step_id != step
        or call.idempotency_key != f"{run_id}:{step}"
        or str(call.call_id) != pin.get("call_id")
        or call.status is not ToolCallStatus.SUCCEEDED
        or str(call.tool_name) != expected_tool
        or str(call.resource) != "gpu"
        or call.model_version != result.model_version
        or call.input_assets != (result.input_asset,)
        or call.output_assets != tuple(asset.ref for asset in assets)
    ):
        raise SubmissionConflict


def _validate_measurements(
    step: str,
    operation: str,
    result,
    measurements: GeometryMeasurements | None,
    segment_payload: dict | None,
) -> None:
    if step == "geometry_segment":
        if measurements is None or measurements.relative_depth is not None:
            raise SubmissionConflict
        x1, y1, x2, y2 = result.mask_region.bbox_xyxy
        expected_fraction = result.area_pixels / (
            result.mask_asset.width * result.mask_asset.height
        )
        if (
            measurements.input_asset != result.input_asset
            or measurements.area_pixels != result.area_pixels
            or measurements.image_plane_fraction != expected_fraction
            or measurements.bbox_width_pixels != x2 - x1
            or measurements.bbox_height_pixels != y2 - y1
        ):
            raise SubmissionConflict
        return
    if operation == "relative_depth":
        if measurements is not None:
            raise SubmissionConflict
        return
    if measurements is None or measurements.relative_depth is None or segment_payload is None:
        raise SubmissionConflict
    try:
        segment = SegmentationResult.model_validate(segment_payload["result"])
        earlier = GeometryMeasurements.model_validate(segment_payload["measurements"])
    except (KeyError, TypeError, ValueError):
        raise SubmissionConflict from None
    if (
        segment.input_asset != result.input_asset
        or (segment.mask_asset.width, segment.mask_asset.height)
        != (result.raw_asset.width, result.raw_asset.height)
        or measurements.input_asset != result.input_asset
        or measurements.area_pixels != earlier.area_pixels
        or measurements.image_plane_fraction != earlier.image_plane_fraction
        or measurements.bbox_width_pixels != earlier.bbox_width_pixels
        or measurements.bbox_height_pixels != earlier.bbox_height_pixels
        or measurements.relative_depth.larger_is != result.larger_is
    ):
        raise SubmissionConflict


def save_geometry(
    repository,
    task_id: UUID,
    step: str,
    result: SegmentationResult | RelativeDepthResult,
    call: ToolCall,
    measurements: GeometryMeasurements | None = None,
) -> dict:
    if step not in {"geometry_segment", "geometry_depth"}:
        raise ValueError("unknown geometry checkpoint step")
    try:
        result = (
            SegmentationResult.model_validate(result.model_dump())
            if step == "geometry_segment"
            else RelativeDepthResult.model_validate(result.model_dump())
        )
        call = ToolCall.model_validate(call.model_dump())
        measurements = (
            None
            if measurements is None
            else GeometryMeasurements.model_validate(measurements.model_dump())
        )
    except (AttributeError, TypeError, ValueError):
        raise SubmissionConflict from None
    payload = {
        "result": result.model_dump(mode="json"),
        "measurements": None
        if measurements is None
        else measurements.model_dump(mode="json"),
    }
    with repository._connect() as connection:
        task_row = connection.execute(
            sql.SQL(
                "SELECT input_asset_id, input_asset_version FROM {} "
                "WHERE task_id=%s FOR UPDATE"
            ).format(repository._table("sewer_tasks")),
            (task_id,),
        ).fetchone()
        if task_row is None:
            raise MissingRecord
        run_row = connection.execute(
            sql.SQL("SELECT run_id, config FROM {} WHERE task_id=%s").format(
                repository._table("sewer_inspection_runs")
            ),
            (task_id,),
        ).fetchone()
        if run_row is None:
            raise MissingRecord
        try:
            geometry = run_row[1]["geometry"]
            if geometry["version"] != "geometry-online-v1":
                raise ValueError
            options = GeometryOptions.model_validate(geometry["options"])
            pin_key = "segment" if step == "geometry_segment" else "depth"
            pin = geometry[pin_key]
            if not isinstance(pin, dict):
                raise ValueError
            if step == "geometry_segment" and options.operation not in {
                "segment",
                "relative_measure",
            }:
                raise ValueError
            if step == "geometry_depth" and options.operation not in {
                "relative_depth",
                "relative_measure",
            }:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise SubmissionConflict from None
        _validate_call(task_id, run_row[0], step, pin, options.operation, result, call)

        checkpoint = connection.execute(
            sql.SQL("SELECT payload FROM {} WHERE task_id=%s AND step=%s").format(
                repository._table("sewer_step_checkpoints")
            ),
            (task_id, step),
        ).fetchone()
        if checkpoint is not None:
            return checkpoint[0]

        source_ref = AssetRef(asset_id=task_row[0], version=task_row[1])
        input_row = connection.execute(
            sql.SQL("SELECT record FROM {} WHERE asset_id=%s AND version=%s").format(
                repository._table("sewer_assets")
            ),
            (source_ref.asset_id, source_ref.version),
        ).fetchone()
        source_row = connection.execute(
            sql.SQL(
                "SELECT payload FROM {} WHERE task_id=%s AND step='geometry_source'"
            ).format(repository._table("sewer_step_checkpoints")),
            (task_id,),
        ).fetchone()
        if input_row is None or source_row is None:
            raise MissingRecord
        try:
            source = AssetRecord.model_validate(input_row[0])
            frozen = source_row[0]
            InspectionDecision.model_validate(frozen["decision"])
            frozen_ref = AssetRef.model_validate(frozen["input_asset"])
            prompt = frozen.get("prompt")
        except (KeyError, TypeError, ValueError):
            raise SubmissionConflict from None
        if (
            source.kind is not AssetKind.IMAGE
            or source.parent is not None
            or source.ref != source_ref
            or frozen_ref != source_ref
            or result.input_asset != source_ref
            or result.model_id != pin.get("model_id")
            or result.model_version != pin.get("model_version")
        ):
            raise SubmissionConflict
        if any(
            (asset.width, asset.height) != (source.width, source.height)
            for asset in _result_assets(result)
        ):
            raise SubmissionConflict
        if step == "geometry_segment":
            if prompt is None or result.prompt.model_dump(mode="json") != prompt:
                raise SubmissionConflict
        elif options.operation == "relative_depth" and prompt is not None:
            raise SubmissionConflict

        segment_row = connection.execute(
            sql.SQL(
                "SELECT payload FROM {} WHERE task_id=%s AND step='geometry_segment'"
            ).format(repository._table("sewer_step_checkpoints")),
            (task_id,),
        ).fetchone()
        _validate_measurements(
            step,
            options.operation,
            result,
            measurements,
            None if segment_row is None else segment_row[0],
        )
        assets = _result_assets(result)
        for asset in assets:
            exists = connection.execute(
                sql.SQL("SELECT 1 FROM {} WHERE asset_id=%s AND version=%s").format(
                    repository._table("sewer_assets")
                ),
                (asset.ref.asset_id, asset.ref.version),
            ).fetchone()
            if exists is not None:
                raise SubmissionConflict
        for asset in assets:
            repository._save_asset(connection, asset)
        repository._record_call(connection, call)
        connection.execute(
            sql.SQL("INSERT INTO {} (task_id, step, payload) VALUES (%s, %s, %s)").format(
                repository._table("sewer_step_checkpoints")
            ),
            (task_id, step, Jsonb(payload)),
        )
    return payload
