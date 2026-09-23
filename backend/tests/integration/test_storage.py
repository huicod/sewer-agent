"""Opt-in PostgreSQL repository tests in one disposable, test-owned schema."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest
from pipe_inspector.config import Settings
from pipe_inspector.contracts.assets import AssetKind, AssetRecord, AssetRef
from pipe_inspector.contracts.intake import TaskSubmission
from pipe_inspector.storage.repository import (
    MissingRecord,
    PostgresRepository,
    SubmissionConflict,
)
from psycopg import sql

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("SEWER_RUN_STORAGE_TEST") != "1",
        reason="explicit opt-in required: uses the local PostgreSQL application database",
    ),
]


@pytest.fixture
def repository():
    settings = Settings()
    schema = "test_storage_" + uuid4().hex
    with psycopg.connect(settings.database_dsn.get_secret_value()) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        store = PostgresRepository(settings.database_dsn, schema=schema)
        store.initialize()
        store.initialize()
        yield store, settings, schema
    finally:
        with psycopg.connect(settings.database_dsn.get_secret_value()) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )


def original_asset(*, kind: AssetKind = AssetKind.IMAGE) -> AssetRecord:
    values = {
        "ref": AssetRef(asset_id=uuid4()),
        "kind": kind,
        "media_type": "image/png",
        "byte_size": 128,
        "width": 16,
        "height": 12,
    }
    if kind == AssetKind.VIDEO:
        values.update(media_type="video/mp4", duration_seconds=1.5)
    return AssetRecord(**values)


def test_reconnect_preserves_references_idempotency_and_session_grouping(repository):
    store, settings, schema = repository
    session = store.create_session()
    asset = original_asset(kind=AssetKind.VIDEO)
    store.save_asset(asset)
    submission = TaskSubmission(
        request_id=uuid4(),
        session_id=session.session_id,
        input_asset=asset.ref,
        instruction="  检查视频中的缺陷  ",
    )

    created = store.create_task(submission)
    replayed = store.create_task(submission)
    reopened = PostgresRepository(settings.database_dsn, schema=schema)

    assert replayed == created
    assert reopened.get_task(created.snapshot.task_id) == created
    assert reopened.get_asset(asset.ref) == asset
    assert reopened.get_session(session.session_id).tasks == (created,)
    assert created.snapshot.runtime_status == "queued"
    assert created.snapshot.inspection_result == "pending"
    assert created.snapshot.stage == "queued"
    assert created.snapshot.stage_progress == 0
    assert created.snapshot.overall_progress == 0
    assert created.created_at.tzinfo is not None
    assert created.snapshot.updated_at.tzinfo is not None

    with pytest.raises(SubmissionConflict, match="^submission conflicts with stored state$"):
        store.create_task(
            TaskSubmission(
                request_id=submission.request_id,
                session_id=session.session_id,
                input_asset=asset.ref,
                instruction="不同指令",
            )
        )


def test_image_mode_reopens_and_participates_in_request_identity(repository):
    store, settings, schema = repository
    session = store.create_session()
    image = original_asset()
    store.save_asset(image)
    submission = TaskSubmission(
        request_id=uuid4(),
        session_id=session.session_id,
        input_asset=image.ref,
        instruction="仅用 YOLO 检查图片",
        inspection_mode="yolo_only",
    )

    created = store.create_task(submission)
    reopened = PostgresRepository(settings.database_dsn, schema=schema)

    assert reopened.get_task(created.snapshot.task_id).inspection_mode == "yolo_only"
    assert reopened.create_task(submission) == created
    with pytest.raises(SubmissionConflict, match="^submission conflicts with stored state$"):
        reopened.create_task(
            TaskSubmission.model_validate(
                submission.model_dump() | {"inspection_mode": "yolo_vlm"}
            )
        )


def test_pure_mode_video_is_rejected_without_creating_task(repository):
    store, _, _ = repository
    session = store.create_session()
    video = original_asset(kind=AssetKind.VIDEO)
    store.save_asset(video)

    with pytest.raises(SubmissionConflict, match="^submission conflicts with stored state$"):
        store.create_task(
            TaskSubmission(
                request_id=uuid4(),
                session_id=session.session_id,
                input_asset=video.ref,
                instruction="仅用 YOLO 检查视频",
                inspection_mode="yolo_only",
            )
        )
    assert store.get_session(session.session_id).tasks == ()


def test_missing_references_are_rejected_without_partial_task(repository):
    store, _, _ = repository
    session = store.create_session()
    missing_asset = AssetRef(asset_id=uuid4())
    submission = TaskSubmission(
        request_id=uuid4(),
        session_id=session.session_id,
        input_asset=missing_asset,
        instruction="检查图片",
    )

    with pytest.raises(MissingRecord, match="^record not found$"):
        store.create_task(submission)
    with pytest.raises(MissingRecord, match="^record not found$"):
        store.get_task(submission.request_id)
    assert store.get_session(session.session_id).tasks == ()

    asset = original_asset()
    store.save_asset(asset)
    with pytest.raises(MissingRecord, match="^record not found$"):
        store.create_task(
            TaskSubmission(
                request_id=uuid4(),
                session_id=uuid4(),
                input_asset=asset.ref,
                instruction="检查图片",
            )
        )


def test_existing_request_id_conflicts_before_new_reference_validation(repository):
    store, _, _ = repository
    session = store.create_session()
    asset = original_asset()
    store.save_asset(asset)
    request_id = uuid4()
    store.create_task(
        TaskSubmission(
            request_id=request_id,
            session_id=session.session_id,
            input_asset=asset.ref,
            instruction="原始指令",
        )
    )

    with pytest.raises(SubmissionConflict, match="^submission conflicts with stored state$"):
        store.create_task(
            TaskSubmission(
                request_id=request_id,
                session_id=uuid4(),
                input_asset=AssetRef(asset_id=uuid4()),
                instruction="篡改后的重放",
            )
        )


def test_initial_task_rejects_derived_or_parented_assets(repository):
    store, _, _ = repository
    session = store.create_session()
    source = original_asset()
    store.save_asset(source)
    derived = AssetRecord(
        ref=AssetRef(asset_id=uuid4()),
        kind=AssetKind.CROP,
        media_type="image/png",
        byte_size=32,
        width=8,
        height=6,
        parent=source.ref,
    )
    parented_image = AssetRecord(
        ref=AssetRef(asset_id=uuid4()),
        kind=AssetKind.IMAGE,
        media_type="image/png",
        byte_size=64,
        width=16,
        height=12,
        parent=source.ref,
    )
    store.save_asset(derived)
    store.save_asset(parented_image)

    for asset in (derived, parented_image):
        with pytest.raises(SubmissionConflict, match="^submission conflicts with stored state$"):
            store.create_task(
                TaskSubmission(
                    request_id=uuid4(),
                    session_id=session.session_id,
                    input_asset=asset.ref,
                    instruction="检查派生图片",
                )
            )


def test_concurrent_same_request_id_creates_one_task(repository):
    store, settings, schema = repository
    session = store.create_session()
    asset = original_asset()
    store.save_asset(asset)
    submission = TaskSubmission(
        request_id=uuid4(),
        session_id=session.session_id,
        input_asset=asset.ref,
        instruction="并发幂等提交",
    )
    workers = 6
    barrier = Barrier(workers)

    def submit_once():
        repository = PostgresRepository(settings.database_dsn, schema=schema)
        barrier.wait(timeout=10)
        return repository.create_task(submission)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        records = list(pool.map(lambda _: submit_once(), range(workers)))

    assert records == [records[0]] * workers
    assert store.get_session(session.session_id).tasks == (records[0],)


def test_asset_version_is_immutable_and_missing_reads_are_safe(repository):
    store, _, _ = repository
    asset = original_asset()
    store.save_asset(asset)
    store.save_asset(asset)
    changed = AssetRecord(**(asset.model_dump() | {"byte_size": asset.byte_size + 1}))

    with pytest.raises(SubmissionConflict, match="^submission conflicts with stored state$"):
        store.save_asset(changed)
    with pytest.raises(MissingRecord, match="^record not found$"):
        store.get_asset(AssetRef(asset_id=uuid4()))
    with pytest.raises(MissingRecord, match="^record not found$"):
        store.get_session(uuid4())


def test_session_timestamp_is_utc(repository):
    store, _, _ = repository
    before = datetime.now(timezone.utc)
    session = store.create_session()
    after = datetime.now(timezone.utc)
    assert before <= session.created_at <= after
