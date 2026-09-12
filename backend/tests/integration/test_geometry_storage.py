"""Opt-in atomic geometry evidence tests in a disposable PostgreSQL schema."""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from pipe_inspector.contracts.assets import AssetRecord, AssetRef
from pipe_inspector.contracts.common import ImageRegion
from pipe_inspector.contracts.geometry import (
    GeometryMeasurements,
    RelativeDepthResult,
    SegmentationResult,
)
from pipe_inspector.contracts.inspection import InspectionDecision
from pipe_inspector.contracts.tools import ToolCall
from pipe_inspector.storage.inspection import InspectionRepository
from pipe_inspector.storage.repository import MissingRecord, SubmissionConflict
from test_inspection_storage import create_task
from test_inspection_storage import inspection_store as inspection_store

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("SEWER_RUN_STORAGE_TEST") != "1",
        reason="explicit opt-in required: uses the local PostgreSQL application database",
    ),
]


def geometry_case(store):
    task, source = create_task(store)
    task_id = task.snapshot.task_id
    call_id = uuid4()
    pin = {
        "version": "geometry-online-v1",
        "options": {
            "operation": "segment",
            "roi_xyxy": [2.0, 1.0, 7.0, 5.0],
            "finding_id": None,
        },
        "segment": {
            "model_id": "facebook/sam2.1-hiera-large",
            "model_version": "sam-revision",
            "checkpoint": "/not-persisted-in-public-result/sam",
            "device": "cuda:0",
            "call_id": str(call_id),
        },
        "depth": None,
    }
    run = store.ensure_run(task_id, {"geometry": pin})
    prompt = ImageRegion(width=16, height=12, bbox_xyxy=(2, 1, 7, 5))
    baseline = InspectionDecision(
        findings=(),
        inspection_result="normal",
        coverage={
            "sufficient_coverage": True,
            "sufficient_quality": True,
            "negative_audit_complete": True,
        },
        needs_review=False,
        reasons=(),
    )
    store.save_step(
        task_id,
        "geometry_source",
        {
            "decision": baseline.model_dump(mode="json"),
            "input_asset": source.ref.model_dump(mode="json"),
            "prompt": prompt.model_dump(mode="json"),
        },
    )
    mask = AssetRecord(
        ref=AssetRef(asset_id=uuid4()),
        kind="mask",
        media_type="image/png",
        byte_size=100,
        width=16,
        height=12,
        parent=source.ref,
    )
    overlay = AssetRecord(
        ref=AssetRef(asset_id=uuid4()),
        kind="mask",
        media_type="image/png",
        byte_size=200,
        width=16,
        height=12,
        parent=source.ref,
    )
    result = SegmentationResult(
        input_asset=source.ref,
        prompt=prompt,
        mask_asset=mask,
        overlay_asset=overlay,
        model_id=pin["segment"]["model_id"],
        model_version=pin["segment"]["model_version"],
        model_score=0.8,
        area_pixels=6,
        mask_region=ImageRegion(width=16, height=12, bbox_xyxy=(3, 2, 6, 4)),
    )
    measurements = GeometryMeasurements(
        input_asset=source.ref,
        area_pixels=6,
        image_plane_fraction=0.03125,
        bbox_width_pixels=3,
        bbox_height_pixels=2,
    )
    call = ToolCall(
        call_id=call_id,
        task_id=task_id,
        step_id="geometry_segment",
        idempotency_key=f"{run['run_id']}:geometry_segment",
        tool_name="segment",
        tool_version="1",
        model_version=result.model_version,
        resource="gpu",
        timeout_seconds=180,
        input_assets=(source.ref,),
        output_assets=(mask.ref, overlay.ref),
        status="succeeded",
    )
    return task, source, result, measurements, call


def test_geometry_save_atomically_roundtrips_two_assets_call_and_checkpoint(
    inspection_store,
):
    store, _, _ = inspection_store
    task, _, result, measurements, call = geometry_case(store)

    saved = store.save_geometry(
        task.snapshot.task_id, "geometry_segment", result, call, measurements
    )

    assert saved == {
        "result": result.model_dump(mode="json"),
        "measurements": measurements.model_dump(mode="json"),
    }
    assert store.load_step(task.snapshot.task_id, "geometry_segment") == saved
    assert store.get_asset(result.mask_asset.ref) == result.mask_asset
    assert store.get_asset(result.overlay_asset.ref) == result.overlay_asset
    assert store.list_calls(task.snapshot.task_id) == (call,)


def test_geometry_retry_returns_committed_winner_without_registering_loser(
    inspection_store,
):
    store, _, _ = inspection_store
    task, _, winner, measurements, call = geometry_case(store)
    loser_mask = AssetRecord.model_validate(
        winner.mask_asset.model_dump(mode="json")
        | {"ref": {"asset_id": str(uuid4()), "version": 1}}
    )
    loser_overlay = AssetRecord.model_validate(
        winner.overlay_asset.model_dump(mode="json")
        | {"ref": {"asset_id": str(uuid4()), "version": 1}}
    )
    loser = SegmentationResult.model_validate(
        winner.model_dump(mode="json")
        | {
            "mask_asset": loser_mask.model_dump(mode="json"),
            "overlay_asset": loser_overlay.model_dump(mode="json"),
        }
    )
    retry = ToolCall.model_validate(
        call.model_dump(mode="json")
        | {
            "attempt": 2,
            "output_assets": [
                loser_mask.ref.model_dump(mode="json"),
                loser_overlay.ref.model_dump(mode="json"),
            ],
        }
    )

    saved = store.save_geometry(
        task.snapshot.task_id, "geometry_segment", winner, call, measurements
    )
    replayed = store.save_geometry(
        task.snapshot.task_id, "geometry_segment", loser, retry, measurements
    )

    assert replayed == saved
    assert store.list_calls(task.snapshot.task_id) == (call,)
    with pytest.raises(MissingRecord):
        store.get_asset(loser_mask.ref)


@pytest.mark.parametrize(
    "mutation",
    ["wrong_call", "wrong_source_prompt", "wrong_measurement", "preexisting_asset"],
)
def test_geometry_conflicts_rollback_all_new_state(inspection_store, mutation):
    store, _, _ = inspection_store
    task, _, result, measurements, call = geometry_case(store)
    if mutation == "wrong_call":
        call = ToolCall.model_validate(
            call.model_dump(mode="json") | {"call_id": str(uuid4())}
        )
    elif mutation == "wrong_source_prompt":
        source = store.load_step(task.snapshot.task_id, "geometry_source")
        source["prompt"]["bbox_xyxy"] = [1, 1, 7, 5]
        with store._connect() as connection:
            from psycopg import sql
            from psycopg.types.json import Jsonb

            connection.execute(
                sql.SQL(
                    "UPDATE {} SET payload=%s WHERE task_id=%s AND step='geometry_source'"
                ).format(store._table("sewer_step_checkpoints")),
                (Jsonb(source), task.snapshot.task_id),
            )
    elif mutation == "wrong_measurement":
        measurements = GeometryMeasurements.model_validate(
            measurements.model_dump(mode="json") | {"area_pixels": 7}
        )
    else:
        store.save_asset(result.mask_asset)

    with pytest.raises(SubmissionConflict):
        store.save_geometry(
            task.snapshot.task_id, "geometry_segment", result, call, measurements
        )

    assert store.load_step(task.snapshot.task_id, "geometry_segment") is None
    assert store.list_calls(task.snapshot.task_id) == ()
    if mutation != "preexisting_asset":
        with pytest.raises(MissingRecord):
            store.get_asset(result.mask_asset.ref)
    with pytest.raises(MissingRecord):
        store.get_asset(result.overlay_asset.ref)


def test_concurrent_geometry_candidates_commit_one_complete_winner(inspection_store):
    store, settings, schema = inspection_store
    task, _, first, measurements, first_call = geometry_case(store)
    second_mask = AssetRecord.model_validate(
        first.mask_asset.model_dump(mode="json")
        | {"ref": {"asset_id": str(uuid4()), "version": 1}}
    )
    second_overlay = AssetRecord.model_validate(
        first.overlay_asset.model_dump(mode="json")
        | {"ref": {"asset_id": str(uuid4()), "version": 1}}
    )
    second = SegmentationResult.model_validate(
        first.model_dump(mode="json")
        | {
            "mask_asset": second_mask.model_dump(mode="json"),
            "overlay_asset": second_overlay.model_dump(mode="json"),
        }
    )
    second_call = ToolCall.model_validate(
        first_call.model_dump(mode="json")
        | {
            "output_assets": [
                second_mask.ref.model_dump(mode="json"),
                second_overlay.ref.model_dump(mode="json"),
            ]
        }
    )
    barrier = Barrier(2)

    def save(candidate):
        reopened = InspectionRepository(settings.database_dsn, schema=schema)
        result, call = candidate
        barrier.wait(timeout=10)
        return reopened.save_geometry(
            task.snapshot.task_id,
            "geometry_segment",
            result,
            call,
            measurements,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        saved = list(pool.map(save, [(first, first_call), (second, second_call)]))

    assert saved[0] == saved[1]
    result = SegmentationResult.model_validate(saved[0]["result"])
    assert store.get_asset(result.mask_asset.ref) == result.mask_asset
    assert store.get_asset(result.overlay_asset.ref) == result.overlay_asset
    assert len(store.list_calls(task.snapshot.task_id)) == 1


def test_standalone_depth_wrong_source_dimensions_rolls_back_all_state(inspection_store):
    store, _, _ = inspection_store
    task, source = create_task(store)
    task_id = task.snapshot.task_id
    call_id = uuid4()
    depth_pin = {
        "model_id": "depth-anything/Depth-Anything-V2-Small-hf",
        "model_version": "depth-revision",
        "checkpoint": "/private/depth",
        "device": "cuda:0",
        "call_id": str(call_id),
    }
    run = store.ensure_run(
        task_id,
        {
            "geometry": {
                "version": "geometry-online-v1",
                "options": {
                    "operation": "relative_depth",
                    "roi_xyxy": None,
                    "finding_id": None,
                },
                "segment": None,
                "depth": depth_pin,
            }
        },
    )
    baseline = InspectionDecision(
        findings=(),
        inspection_result="normal",
        coverage={
            "sufficient_coverage": True,
            "sufficient_quality": True,
            "negative_audit_complete": True,
        },
        needs_review=False,
        reasons=(),
    )
    store.save_step(
        task_id,
        "geometry_source",
        {
            "decision": baseline.model_dump(mode="json"),
            "input_asset": source.ref.model_dump(mode="json"),
            "prompt": None,
        },
    )
    raw = AssetRecord(
        ref=AssetRef(asset_id=uuid4()),
        kind="depth_map",
        media_type="application/x-npy",
        byte_size=64,
        width=4,
        height=3,
        parent=source.ref,
    )
    preview = AssetRecord(
        ref=AssetRef(asset_id=uuid4()),
        kind="depth_map",
        media_type="image/png",
        byte_size=64,
        width=4,
        height=3,
        parent=source.ref,
    )
    result = RelativeDepthResult(
        input_asset=source.ref,
        raw_asset=raw,
        preview_asset=preview,
        model_id=depth_pin["model_id"],
        model_version=depth_pin["model_version"],
        larger_is="nearer",
        raw_min=0,
        raw_max=1,
    )
    call = ToolCall(
        call_id=call_id,
        task_id=task_id,
        step_id="geometry_depth",
        idempotency_key=f"{run['run_id']}:geometry_depth",
        tool_name="relative_depth",
        tool_version="1",
        model_version=result.model_version,
        resource="gpu",
        timeout_seconds=180,
        input_assets=(source.ref,),
        output_assets=(raw.ref, preview.ref),
        status="succeeded",
    )

    with pytest.raises(SubmissionConflict):
        store.save_geometry(task_id, "geometry_depth", result, call)

    assert store.load_step(task_id, "geometry_depth") is None
    assert store.list_calls(task_id) == ()
    for ref in (raw.ref, preview.ref):
        with pytest.raises(MissingRecord):
            store.get_asset(ref)
