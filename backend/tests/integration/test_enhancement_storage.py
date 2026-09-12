"""Opt-in atomic enhancement checkpoint tests using a disposable PostgreSQL schema."""

import os
from concurrent.futures import ThreadPoolExecutor
from math import ceil, floor
from threading import Barrier
from uuid import uuid4

import pytest
from pipe_inspector.contracts.assets import AssetRecord, AssetRef
from pipe_inspector.contracts.common import ImageRegion
from pipe_inspector.contracts.enhancement import EnhancementResult
from pipe_inspector.contracts.inspection import QualitySummary
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


def _quality(value: float) -> QualitySummary:
    return QualitySummary(
        mean_luminance=value,
        contrast=value,
        sharpness=value,
        adequate=True,
    )


def _enhancement_case(store, *, kind="dehaze", roi_xyxy=None, pin_overrides=None):
    task, input_asset = create_task(store)
    task_id = task.snapshot.task_id
    call_id = uuid4()
    model_id = "dehazeformer-mct" if kind == "dehaze" else "real-esrgan-x4plus"
    model_version = f"{model_id}-test.pth"
    enhancement_pin = {
        "version": "enhancement-online-v1",
        "options": {"kind": kind, "roi_xyxy": roi_xyxy},
        "model_id": model_id,
        "model_version": model_version,
        "call_id": str(call_id),
    }
    enhancement_pin.update(pin_overrides or {})
    run = store.ensure_run(task_id, {"enhancement": enhancement_pin})
    if roi_xyxy is None:
        box = [0, 0, input_asset.width, input_asset.height]
    else:
        box = [
            floor(roi_xyxy[0]),
            floor(roi_xyxy[1]),
            ceil(roi_xyxy[2]),
            ceil(roi_xyxy[3]),
        ]
    source_region = ImageRegion(
        width=input_asset.width,
        height=input_asset.height,
        bbox_xyxy=box,
    )
    scale = 4 if kind == "super_resolution" else 1
    output_asset = AssetRecord(
        ref=AssetRef(asset_id=uuid4()),
        kind="enhanced_image",
        media_type="image/png",
        byte_size=256,
        width=(box[2] - box[0]) * scale,
        height=(box[3] - box[1]) * scale,
        parent=input_asset.ref,
    )
    result = EnhancementResult(
        input_asset=input_asset.ref,
        output_asset=output_asset,
        kind=kind,
        model_id=model_id,
        model_version=model_version,
        source_region=source_region,
        quality_before=_quality(1),
        quality_after=_quality(2),
    )
    call = ToolCall(
        call_id=call_id,
        task_id=task_id,
        step_id="enhance",
        idempotency_key=f"{run['run_id']}:enhance",
        tool_name="enhance",
        tool_version="enhancement-online-v1",
        model_version=model_version,
        resource="gpu",
        timeout_seconds=180,
        input_assets=(input_asset.ref,),
        output_assets=(output_asset.ref,),
        status="succeeded",
    )
    return task, input_asset, result, call


def test_save_enhancement_roundtrips_asset_call_and_checkpoint(inspection_store):
    store, _, _ = inspection_store
    task, _, result, call = _enhancement_case(store)

    saved = store.save_enhancement(task.snapshot.task_id, result, call)

    expected = result.model_dump(mode="json")
    assert saved == expected
    assert store.load_step(task.snapshot.task_id, "enhance") == expected
    assert store.get_asset(result.output_asset.ref) == result.output_asset
    assert store.list_calls(task.snapshot.task_id) == (call,)


@pytest.mark.parametrize(
    ("kind", "roi_xyxy", "expected_box", "expected_size"),
    [
        ("deblur", [1.2, 2.8, 7.1, 9.2], (1, 2, 8, 10), (7, 8)),
        ("super_resolution", [1.2, 2.8, 7.1, 9.2], (1, 2, 8, 10), (28, 32)),
    ],
)
def test_enhancement_dimensions_follow_rounded_roi_and_kind_scale(
    inspection_store, kind, roi_xyxy, expected_box, expected_size
):
    store, _, _ = inspection_store
    task, _, result, call = _enhancement_case(
        store, kind=kind, roi_xyxy=roi_xyxy
    )

    saved = store.save_enhancement(task.snapshot.task_id, result, call)

    assert tuple(saved["source_region"]["bbox_xyxy"]) == expected_box
    assert (
        saved["output_asset"]["width"],
        saved["output_asset"]["height"],
    ) == expected_size


def test_retry_with_new_output_returns_winner_without_registering_retry(inspection_store):
    store, _, _ = inspection_store
    task, _, winner, first_call = _enhancement_case(store)
    retry_output = AssetRecord.model_validate(
        winner.output_asset.model_dump(mode="json")
        | {"ref": {"asset_id": str(uuid4()), "version": 1}}
    )
    retry = EnhancementResult.model_validate(
        winner.model_dump(mode="json") | {"output_asset": retry_output.model_dump(mode="json")}
    )
    retry_call = ToolCall.model_validate(
        first_call.model_dump(mode="json")
        | {
            "attempt": 2,
            "output_assets": [retry.output_asset.ref.model_dump(mode="json")],
        }
    )

    saved = store.save_enhancement(task.snapshot.task_id, winner, first_call)
    replayed = store.save_enhancement(task.snapshot.task_id, retry, retry_call)

    assert replayed == saved == winner.model_dump(mode="json")
    assert store.list_calls(task.snapshot.task_id) == (first_call,)
    with pytest.raises(MissingRecord):
        store.get_asset(retry.output_asset.ref)


def test_retry_rejects_wrong_logical_call_without_changing_winner(inspection_store):
    store, _, _ = inspection_store
    task, _, winner, call = _enhancement_case(store)
    saved = store.save_enhancement(task.snapshot.task_id, winner, call)
    wrong_call = ToolCall.model_validate(
        call.model_dump(mode="json") | {"call_id": str(uuid4())}
    )

    with pytest.raises(SubmissionConflict):
        store.save_enhancement(task.snapshot.task_id, winner, wrong_call)

    assert store.load_step(task.snapshot.task_id, "enhance") == saved
    assert store.list_calls(task.snapshot.task_id) == (call,)


def test_concurrent_candidates_share_one_winner_asset_and_call(inspection_store):
    store, settings, schema = inspection_store
    task, _, first, first_call = _enhancement_case(store)
    second_output = AssetRecord.model_validate(
        first.output_asset.model_dump(mode="json")
        | {"ref": {"asset_id": str(uuid4()), "version": 1}}
    )
    second = EnhancementResult.model_validate(
        first.model_dump(mode="json") | {"output_asset": second_output.model_dump(mode="json")}
    )
    second_call = ToolCall.model_validate(
        first_call.model_dump(mode="json")
        | {"output_assets": [second_output.ref.model_dump(mode="json")]}
    )
    barrier = Barrier(2)

    def save(candidate):
        result, call = candidate
        reopened = InspectionRepository(settings.database_dsn, schema=schema)
        barrier.wait(timeout=10)
        return reopened.save_enhancement(task.snapshot.task_id, result, call)

    with ThreadPoolExecutor(max_workers=2) as pool:
        saved = list(pool.map(save, [(first, first_call), (second, second_call)]))

    assert saved[0] == saved[1]
    winner_ref = AssetRef.model_validate(saved[0]["output_asset"]["ref"])
    loser_ref = (
        second_output.ref
        if winner_ref == first.output_asset.ref
        else first.output_asset.ref
    )
    assert store.get_asset(winner_ref).ref == winner_ref
    with pytest.raises(MissingRecord):
        store.get_asset(loser_ref)
    (winner_call,) = store.list_calls(task.snapshot.task_id)
    assert winner_call.output_assets == (winner_ref,)


def test_late_conflicting_call_identity_rolls_back_asset_and_checkpoint(inspection_store):
    store, _, _ = inspection_store
    task, _, result, call = _enhancement_case(store)
    other_task, other_asset = create_task(store)
    conflicting = ToolCall.model_validate(
        call.model_dump(mode="json")
        | {
            "task_id": str(other_task.snapshot.task_id),
            "step_id": "detect",
            "idempotency_key": f"detect:{other_task.snapshot.task_id}",
            "tool_name": "detect",
            "input_assets": [other_asset.ref.model_dump(mode="json")],
            "output_assets": [],
        }
    )
    store.record_call(conflicting)

    with pytest.raises(SubmissionConflict):
        store.save_enhancement(task.snapshot.task_id, result, call)

    assert store.load_step(task.snapshot.task_id, "enhance") is None
    assert store.list_calls(task.snapshot.task_id) == ()
    with pytest.raises(MissingRecord):
        store.get_asset(result.output_asset.ref)
    assert store.list_calls(other_task.snapshot.task_id) == (conflicting,)


def test_first_write_rejects_pre_registered_output_asset(inspection_store):
    store, _, _ = inspection_store
    task, _, result, call = _enhancement_case(store)
    store.save_asset(result.output_asset)

    with pytest.raises(SubmissionConflict):
        store.save_enhancement(task.snapshot.task_id, result, call)

    assert store.load_step(task.snapshot.task_id, "enhance") is None
    assert store.list_calls(task.snapshot.task_id) == ()
    assert store.get_asset(result.output_asset.ref) == result.output_asset


def test_first_write_rejects_super_resolution_pin_without_roi(inspection_store):
    store, _, _ = inspection_store
    task, _, result, call = _enhancement_case(store, kind="super_resolution")

    with pytest.raises(SubmissionConflict):
        store.save_enhancement(task.snapshot.task_id, result, call)

    assert store.load_step(task.snapshot.task_id, "enhance") is None
    assert store.list_calls(task.snapshot.task_id) == ()
    with pytest.raises(MissingRecord):
        store.get_asset(result.output_asset.ref)


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_call_id",
        "wrong_idempotency_key",
        "wrong_tool",
        "wrong_resource",
        "wrong_call_model",
        "wrong_call_input",
        "wrong_call_output",
    ],
)
def test_first_write_rejects_call_contract_mismatches_without_partial_state(
    inspection_store, mutation
):
    store, _, _ = inspection_store
    task, _, result, call = _enhancement_case(store)
    updates = {
        "wrong_call_id": {"call_id": str(uuid4())},
        "wrong_idempotency_key": {"idempotency_key": "other:enhance"},
        "wrong_tool": {"tool_name": "verify"},
        "wrong_resource": {"resource": "cpu"},
        "wrong_call_model": {"model_version": "unexpected-version"},
        "wrong_call_input": {"input_assets": []},
        "wrong_call_output": {"output_assets": []},
    }
    bad_call = ToolCall.model_validate(
        call.model_dump(mode="json") | updates[mutation]
    )

    with pytest.raises(SubmissionConflict):
        store.save_enhancement(task.snapshot.task_id, result, bad_call)

    assert store.load_step(task.snapshot.task_id, "enhance") is None
    assert store.list_calls(task.snapshot.task_id) == ()
    with pytest.raises(MissingRecord):
        store.get_asset(result.output_asset.ref)


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_task",
        "wrong_input",
        "wrong_parent",
        "wrong_roi",
        "wrong_model",
        "wrong_model_version",
        "wrong_pin",
        "wrong_status",
        "wrong_output_size",
    ],
)
def test_first_write_rejects_invalid_enhancement_without_partial_state(
    inspection_store, mutation
):
    store, _, _ = inspection_store
    pin_overrides = {"version": "enhancement-online-v2"} if mutation == "wrong_pin" else None
    task, _, result, call = _enhancement_case(store, pin_overrides=pin_overrides)
    output_ref = result.output_asset.ref

    if mutation == "wrong_task":
        other, _ = create_task(store)
        call = ToolCall.model_validate(
            call.model_dump(mode="json") | {"task_id": str(other.snapshot.task_id)}
        )
    elif mutation == "wrong_input":
        _, foreign = create_task(store)
        output = AssetRecord.model_validate(
            result.output_asset.model_dump(mode="json")
            | {"parent": foreign.ref.model_dump(mode="json")}
        )
        result = EnhancementResult.model_validate(
            result.model_dump(mode="json")
            | {
                "input_asset": foreign.ref.model_dump(mode="json"),
                "output_asset": output.model_dump(mode="json"),
            }
        )
        call = ToolCall.model_validate(
            call.model_dump(mode="json")
            | {
                "input_assets": [foreign.ref.model_dump(mode="json")],
                "output_assets": [output.ref.model_dump(mode="json")],
            }
        )
    elif mutation == "wrong_parent":
        _, foreign = create_task(store)
        bad_output = AssetRecord.model_construct(
            ref=result.output_asset.ref,
            kind=result.output_asset.kind,
            media_type=result.output_asset.media_type,
            byte_size=result.output_asset.byte_size,
            width=result.output_asset.width,
            height=result.output_asset.height,
            duration_seconds=None,
            parent=foreign.ref,
        )
        result = EnhancementResult.model_construct(
            schema_version=result.schema_version,
            input_asset=result.input_asset,
            output_asset=bad_output,
            kind=result.kind,
            model_id=result.model_id,
            model_version=result.model_version,
            source_region=result.source_region,
            quality_before=result.quality_before,
            quality_after=result.quality_after,
            review_required=True,
        )
    elif mutation == "wrong_roi":
        region = ImageRegion(width=16, height=12, bbox_xyxy=(1, 0, 16, 12))
        bad_output = AssetRecord.model_validate(
            result.output_asset.model_dump(mode="json") | {"width": 15}
        )
        result = EnhancementResult.model_validate(
            result.model_dump(mode="json")
            | {
                "source_region": region.model_dump(mode="json"),
                "output_asset": bad_output.model_dump(mode="json"),
            }
        )
        call = ToolCall.model_validate(
            call.model_dump(mode="json")
            | {"output_assets": [bad_output.ref.model_dump(mode="json")]}
        )
    elif mutation == "wrong_model":
        result = EnhancementResult.model_validate(
            result.model_dump(mode="json") | {"model_id": "unexpected-model"}
        )
    elif mutation == "wrong_model_version":
        result = EnhancementResult.model_validate(
            result.model_dump(mode="json") | {"model_version": "unexpected-version"}
        )
    elif mutation == "wrong_status":
        call = ToolCall.model_validate(
            call.model_dump(mode="json") | {"status": "running"}
        )
    elif mutation == "wrong_output_size":
        bad_output = AssetRecord.model_validate(
            result.output_asset.model_dump(mode="json") | {"width": 15}
        )
        result = EnhancementResult.model_validate(
            result.model_dump(mode="json")
            | {"output_asset": bad_output.model_dump(mode="json")}
        )
        call = ToolCall.model_validate(
            call.model_dump(mode="json")
            | {"output_assets": [bad_output.ref.model_dump(mode="json")]}
        )

    with pytest.raises((ValueError, SubmissionConflict)):
        store.save_enhancement(task.snapshot.task_id, result, call)

    assert store.load_step(task.snapshot.task_id, "enhance") is None
    assert store.list_calls(task.snapshot.task_id) == ()
    with pytest.raises(MissingRecord):
        store.get_asset(output_ref)
