"""Opt-in PostgreSQL tests for atomic selected-video-frame tool admission."""

import os
from datetime import datetime, timezone
from uuid import uuid4

import psycopg
import pytest
from pipe_inspector.config import Settings
from pipe_inspector.contracts.assets import AssetKind, AssetRecord, AssetRef
from pipe_inspector.contracts.enhancement import EnhancementOptions
from pipe_inspector.contracts.inspection import ImageReview, InspectionDecision
from pipe_inspector.contracts.intake import TaskSubmission
from pipe_inspector.contracts.tasks import TaskSnapshot
from pipe_inspector.contracts.turns import TurnSubmission
from pipe_inspector.contracts.video import FrameObservation, VideoAggregationState
from pipe_inspector.storage.repository import SubmissionConflict
from pipe_inspector.storage.supplements import SupplementNotAllowed
from pipe_inspector.storage.video import VideoRepository
from pipe_inspector.workflow.enhancement_config import build_enhancement_config
from pipe_inspector.workflow.start import run_config
from psycopg import sql

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("SEWER_RUN_STORAGE_TEST") != "1",
        reason="explicit opt-in required: uses a disposable PostgreSQL schema",
    ),
]


@pytest.fixture
def frame_store():
    settings = Settings()
    schema = "test_video_frame_tool_" + uuid4().hex
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


def frame_observation(index=7, timestamp=0.7):
    return FrameObservation.model_validate(
        {
            "frame_index": index,
            "timestamp_seconds": timestamp,
            "detection": {
                "detector_id": "fixture",
                "model_version": "v1",
                "label_space_version": "five",
                "width": 32,
                "height": 24,
                "detections": [],
            },
            "quality": {
                "mean_luminance": 100,
                "contrast": 20,
                "sharpness": 50,
                "adequate": True,
            },
        }
    )


def admitted_parent(store):
    session = store.create_session()
    video = AssetRecord(
        ref=AssetRef(asset_id=uuid4()),
        kind=AssetKind.VIDEO,
        media_type="video/mp4",
        byte_size=100,
        width=32,
        height=24,
        duration_seconds=2,
    )
    frame = AssetRecord(
        ref=AssetRef(asset_id=uuid4()),
        kind=AssetKind.FRAME,
        media_type="image/png",
        byte_size=100,
        width=32,
        height=24,
        parent=video.ref,
    )
    store.save_asset(video)
    store.save_asset(frame)
    parent = store.create_task(
        TaskSubmission(
            request_id=uuid4(),
            session_id=session.session_id,
            input_asset=video.ref,
            instruction="检查视频",
        )
    )
    observation = frame_observation()
    store.ensure_run(parent.snapshot.task_id, {"operation": "video_inspection"})
    store.ensure_video_state(
        parent.snapshot.task_id,
        VideoAggregationState(
            source=video.ref,
            detector_id="fixture",
            model_version="v1",
            label_space_version="five",
        ),
    )
    store.commit_video_chunk(
        parent.snapshot.task_id,
        tuple(frame_observation(index, index / 10) for index in range(8)),
    )
    review = ImageReview(
        image_assessable=True,
        full_image_audited=True,
        candidates=(),
        additional_anomalies=(),
        summary="该帧无明显异常",
    )
    store.save_step(
        parent.snapshot.task_id,
        "video_evidence",
        {
            "frames": [
                {
                    "frame_index": 7,
                    "timestamp_seconds": 0.7,
                    "asset": frame.ref.model_dump(mode="json"),
                }
            ],
            "review_frames": [7],
        },
    )
    store.save_step(
        parent.snapshot.task_id,
        "video_verify:7",
        {"review": review.model_dump(mode="json")},
    )
    completed = TaskSnapshot.model_validate(
        parent.snapshot.model_dump()
        | {
            "runtime_status": "completed",
            "stage": "finished",
            "inspection_result": "normal",
            "coverage": {
                "sufficient_coverage": True,
                "sufficient_quality": True,
                "negative_audit_complete": True,
            },
            "updated_at": datetime.now(timezone.utc),
        }
    )
    parent = store.update_snapshot(completed)
    return parent, video, frame, observation, review


def child_config(settings, parent, video, frame, observation, **pin_changes):
    config = run_config(settings, detector=observation.detection, force_review=True)
    config.update(
        operation="supplement_review",
        parent_task_id=str(parent.snapshot.task_id),
        supplement_policy_version="supplement-policy-v2",
        frame_source={
            "version": "video-frame-tool-v1",
            "parent_task_id": str(parent.snapshot.task_id),
            "video_asset": video.ref.model_dump(mode="json"),
            "frame_asset": frame.ref.model_dump(mode="json"),
            "frame_index": 7,
            "timestamp_seconds": 0.7,
            "width": 32,
            "height": 24,
            "operation": "enhance",
        }
        | pin_changes,
        enhancement=build_enhancement_config(
            settings, EnhancementOptions(kind="dehaze"), check_files=False
        ),
    )
    return config


def child_submission(parent, frame, **changes):
    return TaskSubmission.model_validate(
        {
            "request_id": uuid4(),
            "session_id": parent.session_id,
            "input_asset": frame.ref.model_dump(mode="json"),
            "instruction": "对第7帧去雾",
        }
        | changes
    )


def test_atomic_frame_admission_reopens_frozen_source_and_local_baseline(frame_store):
    store, settings, schema = frame_store
    parent, video, frame, observation, review = admitted_parent(store)
    request = child_submission(parent, frame)
    config = child_config(settings, parent, video, frame, observation)
    parent_before = parent.model_dump(mode="json")

    child = store.admit_frame_tool(parent.snapshot.task_id, request, config)
    replayed = VideoRepository(settings.database_dsn, schema=schema).admit_frame_tool(
        parent.snapshot.task_id,
        request,
        config | {"mutable_caller_pin": "changed"},
    )

    assert replayed == child
    assert child.parent_task_id == parent.snapshot.task_id
    assert child.frame_source.video_asset == video.ref
    assert child.frame_source.frame_index == 7
    assert store.get_run(child.snapshot.task_id)["config"] == config
    assert store.load_step(child.snapshot.task_id, "detect") == {
        "detection": observation.detection.model_dump(mode="json"),
        "quality": observation.quality.model_dump(mode="json"),
    }
    assert store.load_step(child.snapshot.task_id, "source") == {
        "task": parent_before,
        "verify": None,
    }
    frozen = store.load_step(child.snapshot.task_id, "frame_source")
    assert frozen["pin"] == config["frame_source"]
    assert frozen["observation"] == observation.model_dump(mode="json")
    assert frozen["video_verify"] == {"review": review.model_dump(mode="json")}
    decision = InspectionDecision.model_validate(frozen["decision"])
    assert decision.inspection_result == "normal" and not decision.needs_review
    assert store.get_task(parent.snapshot.task_id).model_dump(mode="json") == parent_before


@pytest.mark.parametrize(
    "pin_changes",
    [
        {"frame_index": 8},
        {"timestamp_seconds": 0.8},
        {"width": 31},
        {"video_asset": {"asset_id": str(uuid4()), "version": 1}},
    ],
)
def test_frame_admission_rejects_mismatched_frozen_provenance(frame_store, pin_changes):
    store, settings, _ = frame_store
    parent, video, frame, observation, _ = admitted_parent(store)
    with pytest.raises(SubmissionConflict):
        store.admit_frame_tool(
            parent.snapshot.task_id,
            child_submission(parent, frame),
            child_config(settings, parent, video, frame, observation, **pin_changes),
        )


def test_frame_operation_quota_is_independent_from_image_supplement_slot(frame_store):
    store, settings, _ = frame_store
    parent, video, frame, observation, _ = admitted_parent(store)
    config = child_config(settings, parent, video, frame, observation)
    store.admit_frame_tool(parent.snapshot.task_id, child_submission(parent, frame), config)

    with pytest.raises(SupplementNotAllowed, match="frame_tool_limit"):
        store.admit_frame_tool(
            parent.snapshot.task_id,
            child_submission(parent, frame),
            config,
        )
    with store._connect() as connection:
        assert (
            connection.execute(
                sql.SQL("SELECT count(*) FROM {}").format(store._table("sewer_image_supplements"))
            ).fetchone()[0]
            == 0
        )


def test_turn_admission_accepts_only_evidence_frame_bound_to_selected_video_task(frame_store):
    store, _, _ = frame_store
    parent, video, frame, _, _ = admitted_parent(store)
    other_frame = AssetRecord(
        ref=AssetRef(asset_id=uuid4()),
        kind=AssetKind.FRAME,
        media_type="image/png",
        byte_size=100,
        width=32,
        height=24,
        parent=video.ref,
    )
    store.save_asset(other_frame)

    with pytest.raises(SubmissionConflict):
        store.admit_turn(
            TurnSubmission(
                request_id=uuid4(),
                session_id=parent.session_id,
                text="增强未选中的帧",
                task_id=parent.snapshot.task_id,
                input_asset=other_frame.ref,
            ),
            {},
        )

    accepted = store.admit_turn(
        TurnSubmission(
            request_id=uuid4(),
            session_id=parent.session_id,
            text="增强选中的帧",
            task_id=parent.snapshot.task_id,
            input_asset=frame.ref,
        ),
        {},
    )
    assert accepted.task_id == parent.snapshot.task_id
    assert accepted.input_asset == frame.ref
