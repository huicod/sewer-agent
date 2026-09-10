"""Opt-in PostgreSQL tests for durable video scan checkpoints."""

import os
from datetime import datetime, timezone
from uuid import uuid4

import psycopg
import pytest
from pipe_inspector.config import Settings
from pipe_inspector.contracts.assets import AssetKind, AssetRecord, AssetRef
from pipe_inspector.contracts.intake import TaskSubmission
from pipe_inspector.contracts.tasks import TaskSnapshot
from pipe_inspector.contracts.video import (
    FrameObservation,
    VideoAggregationConfig,
    VideoAggregationState,
)
from pipe_inspector.storage.repository import MissingRecord, SubmissionConflict
from pipe_inspector.storage.video import VideoRepository
from psycopg import sql

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("SEWER_RUN_STORAGE_TEST") != "1",
        reason="explicit opt-in required: uses the local PostgreSQL application database",
    ),
]


@pytest.fixture
def video_store():
    settings = Settings()
    schema = "test_video_storage_" + uuid4().hex
    with psycopg.connect(settings.database_dsn.get_secret_value()) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        store = VideoRepository(settings.database_dsn, schema=schema)
        store.initialize()
        store.initialize()
        yield store, settings, schema
    finally:
        with psycopg.connect(settings.database_dsn.get_secret_value()) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def create_task(store, *, kind=AssetKind.VIDEO, duration=20.0):
    session = store.create_session()
    asset = AssetRecord(
        ref=AssetRef(asset_id=uuid4()),
        kind=kind,
        media_type="video/mp4" if kind == AssetKind.VIDEO else "image/png",
        byte_size=256,
        width=32,
        height=24,
        duration_seconds=duration if kind == AssetKind.VIDEO else None,
    )
    store.save_asset(asset)
    task = store.create_task(
        TaskSubmission(
            request_id=uuid4(),
            session_id=session.session_id,
            input_asset=asset.ref,
            instruction="scan every decodable video frame",
        )
    )
    return task, asset


def initial(source, **changes):
    return VideoAggregationState(
        source=source,
        detector_id="fixture-detector",
        model_version="fixture-v1",
        label_space_version="five-v1",
        **changes,
    )


def frame(index, *, timestamp, label="PL", score=0.7):
    return FrameObservation.model_validate(
        {
            "frame_index": index,
            "timestamp_seconds": timestamp,
            "detection": {
                "detector_id": "fixture-detector",
                "model_version": "fixture-v1",
                "label_space_version": "five-v1",
                "width": 32,
                "height": 24,
                "detections": []
                if label is None
                else [
                    {
                        "label": label,
                        "native_label": label,
                        "score": score,
                        "bbox_xyxy": [1, 2, 12, 14],
                    }
                ],
            },
            "quality": {
                "mean_luminance": 100,
                "contrast": 20,
                "sharpness": 50,
                "adequate": True,
            },
        }
    )


def test_state_requires_admitted_video_and_reopen_keeps_identity_pins(video_store):
    store, settings, schema = video_store
    task, asset = create_task(store)
    candidate = initial(asset.ref)

    assert store.load_video_state(task.snapshot.task_id) is None
    assert store.ensure_video_state(task.snapshot.task_id, candidate) == candidate
    reopened = VideoRepository(settings.database_dsn, schema=schema)
    assert reopened.load_video_state(task.snapshot.task_id) == candidate
    assert reopened.ensure_video_state(task.snapshot.task_id, candidate) == candidate

    other_task, other_asset = create_task(store)
    with pytest.raises(SubmissionConflict):
        store.ensure_video_state(task.snapshot.task_id, initial(other_asset.ref))
    with pytest.raises(SubmissionConflict):
        store.ensure_video_state(
            task.snapshot.task_id,
            initial(
                asset.ref,
                config=VideoAggregationConfig(max_gap_frames=9),
            ),
        )
    with pytest.raises(SubmissionConflict):
        store.ensure_video_state(
            task.snapshot.task_id,
            VideoAggregationState(
                source=asset.ref,
                detector_id="different-detector",
                model_version="fixture-v1",
                label_space_version="five-v1",
            ),
        )

    image_task, _ = create_task(store, kind=AssetKind.IMAGE)
    with pytest.raises(SubmissionConflict):
        store.ensure_video_state(image_task.snapshot.task_id, initial(image_task.input_asset))
    with pytest.raises(MissingRecord):
        store.ensure_video_state(uuid4(), candidate)
    assert other_task.input_asset == other_asset.ref


def test_contiguous_commits_replay_without_recount_and_preserve_pts_progress(video_store):
    store, _, _ = video_store
    task, asset = create_task(store, duration=20)
    task_id = task.snapshot.task_id
    store.ensure_video_state(task_id, initial(asset.ref))
    first = (frame(0, timestamp=7.1), frame(1, timestamp=8.1))
    second = (frame(2, timestamp=9.1, label=None),)

    after_first = store.commit_video_chunk(task_id, first)
    after_second = store.commit_video_chunk(task_id, second, attempt=3)
    replayed = store.commit_video_chunk(task_id, first, attempt=4)

    assert after_first.next_frame == 2
    assert replayed == after_second
    assert after_second.next_frame == 3
    assert store.get_video_frame(task_id, 0) == first[0]
    assert store.get_video_frame(task_id, 2) == second[0]
    snapshot = store.get_task(task_id).snapshot
    assert snapshot.runtime_status == "running"
    assert snapshot.stage == "detecting"
    assert snapshot.processed_frames == 3
    assert snapshot.processed_chunks == 2
    assert snapshot.retry_count == 2
    assert snapshot.overall_progress == pytest.approx(0.12)
    assert snapshot.overall_progress < 0.8


def test_gap_partial_overlap_changed_replay_and_missing_frame_are_refused(video_store):
    store, _, _ = video_store
    task, asset = create_task(store)
    task_id = task.snapshot.task_id
    store.ensure_video_state(task_id, initial(asset.ref))
    first = (frame(0, timestamp=0), frame(1, timestamp=0.1))
    committed = store.commit_video_chunk(task_id, first)

    with pytest.raises(SubmissionConflict):
        store.commit_video_chunk(task_id, (frame(1, timestamp=0.1), frame(2, timestamp=0.2)))
    with pytest.raises(SubmissionConflict):
        store.commit_video_chunk(task_id, (frame(3, timestamp=0.3),))
    with pytest.raises(SubmissionConflict):
        store.commit_video_chunk(task_id, (frame(0, timestamp=0, score=0.9),))
    with pytest.raises(MissingRecord):
        store.get_video_frame(task_id, 99)

    assert store.load_video_state(task_id) == committed
    assert store.get_task(task_id).snapshot.processed_chunks == 1


def test_finalization_is_immutable_freezes_head_and_does_not_finish_whole_task(video_store):
    store, _, _ = video_store
    task, asset = create_task(store)
    task_id = task.snapshot.task_id
    store.ensure_video_state(task_id, initial(asset.ref))
    store.commit_video_chunk(task_id, (frame(0, timestamp=4.2),))

    result = store.finish_video_scan(task_id)
    replayed = store.finish_video_scan(task_id, error_code="late_error")

    assert replayed == result
    assert result.decode_complete is True
    assert result.error_code is None
    assert result.inspection_result == "pending"
    assert result.state.finalized and not result.state.active
    assert len(result.state.events) == 1
    assert store.load_step(task_id, "video_scan") == result.model_dump(mode="json")
    snapshot = store.get_task(task_id).snapshot
    assert snapshot.runtime_status == "running"
    assert snapshot.inspection_result == "pending"
    assert snapshot.overall_progress < 1
    with pytest.raises(SubmissionConflict):
        store.commit_video_chunk(task_id, (frame(1, timestamp=4.3),))


def test_terminal_task_rejects_first_finalization_without_mutating_empty_head(video_store):
    store, _, _ = video_store
    task, asset = create_task(store)
    task_id = task.snapshot.task_id
    empty = store.ensure_video_state(task_id, initial(asset.ref))
    cancelled = TaskSnapshot.model_validate(
        task.snapshot.model_dump()
        | {
            "runtime_status": "cancelled",
            "stage": "finished",
            "updated_at": datetime.now(timezone.utc),
        }
    )
    store.update_snapshot(cancelled)

    with pytest.raises(SubmissionConflict):
        store.commit_video_chunk(task_id, (frame(0, timestamp=0),))
    with pytest.raises(SubmissionConflict):
        store.finish_video_scan(task_id, error_code="cancelled")

    assert store.load_video_state(task_id) == empty
    assert store.load_step(task_id, "video_scan") is None
    assert store.get_task(task_id).snapshot == cancelled


def test_terminal_state_allows_readonly_reopen_and_cached_final_result(video_store):
    store, _, _ = video_store
    task, asset = create_task(store)
    task_id = task.snapshot.task_id
    candidate = initial(asset.ref)
    store.ensure_video_state(task_id, candidate)
    store.commit_video_chunk(task_id, (frame(0, timestamp=0),))
    saved = store.finish_video_scan(task_id)
    current = store.get_task(task_id).snapshot
    cancelled = TaskSnapshot.model_validate(
        current.model_dump()
        | {
            "runtime_status": "cancelled",
            "stage": "finished",
            "can_cancel": False,
            "updated_at": datetime.now(timezone.utc),
        }
    )
    store.update_snapshot(cancelled)

    assert store.ensure_video_state(task_id, candidate) == saved.state
    assert store.finish_video_scan(task_id, error_code="late_error") == saved
    assert store.load_video_state(task_id) == saved.state
    assert store.get_task(task_id).snapshot == cancelled


def test_terminal_task_cannot_create_a_new_video_head(video_store):
    store, _, _ = video_store
    task, asset = create_task(store)
    terminal = TaskSnapshot.model_validate(
        task.snapshot.model_dump()
        | {
            "runtime_status": "cancelled",
            "stage": "finished",
            "updated_at": datetime.now(timezone.utc),
        }
    )
    store.update_snapshot(terminal)

    with pytest.raises(SubmissionConflict):
        store.ensure_video_state(task.snapshot.task_id, initial(asset.ref))

    assert store.load_video_state(task.snapshot.task_id) is None
