"""Opt-in PostgreSQL tests for atomic, immutable human-review audit records."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest
from pipe_inspector.config import Settings
from pipe_inspector.contracts.assets import AssetKind, AssetRecord, AssetRef
from pipe_inspector.contracts.findings import EvidenceRef, Finding
from pipe_inspector.contracts.human_review import HumanReviewSubmission
from pipe_inspector.contracts.intake import TaskSubmission
from pipe_inspector.contracts.tasks import CoverageSummary, TaskSnapshot
from pipe_inspector.storage.human_review import HumanReviewRepository
from pipe_inspector.storage.repository import SubmissionConflict
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
    schema = "test_human_review_" + uuid4().hex
    with psycopg.connect(settings.database_dsn.get_secret_value()) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        store = HumanReviewRepository(settings.database_dsn, schema=schema)
        store.initialize()
        store.initialize()
        yield store, settings, schema
    finally:
        with psycopg.connect(settings.database_dsn.get_secret_value()) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def create_waiting_task(store):
    session = store.create_session()
    asset = AssetRecord(
        ref=AssetRef(asset_id=uuid4()),
        kind=AssetKind.IMAGE,
        media_type="image/png",
        byte_size=128,
        width=16,
        height=12,
    )
    store.save_asset(asset)
    task = store.create_task(
        TaskSubmission(
            request_id=uuid4(),
            session_id=session.session_id,
            input_asset=asset.ref,
            instruction="检查图像",
        )
    )
    finding = Finding(
        finding_id=uuid4(),
        task_id=task.snapshot.task_id,
        canonical_label="AJ",
        review_status="needs_review",
        source="detector",
        score=0.8,
        evidence=(EvidenceRef(asset=asset.ref),),
    )
    snapshot = TaskSnapshot(
        task_id=task.snapshot.task_id,
        runtime_status="waiting_review",
        inspection_result="inconclusive",
        stage="human_review",
        stage_progress=1,
        overall_progress=0.95,
        waiting_reason="待人工确认",
        updated_at=datetime.now(timezone.utc),
        findings=(finding,),
        coverage=CoverageSummary(sufficient_coverage=True, sufficient_quality=True),
    )
    store.update_snapshot(snapshot)
    return task.snapshot.task_id, snapshot


def submission(snapshot, *, request_id=None, note=""):
    return HumanReviewSubmission(
        request_id=request_id or uuid4(),
        reviewer="huicod",
        snapshot_updated_at=snapshot.updated_at,
        decisions=(
            {
                "finding_id": snapshot.findings[0].finding_id,
                "action": "reject",
                "reason": "人工判定不是缺陷",
            },
        ),
        negative_audit_complete=True,
        note=note,
    )


def test_review_audit_is_atomic_persistent_and_identically_retryable(repository):
    store, settings, schema = repository
    task_id, before = create_waiting_task(store)
    body = submission(before)

    created = store.apply_human_review(task_id, body)
    replayed = store.apply_human_review(task_id, body)
    reopened = HumanReviewRepository(settings.database_dsn, schema=schema)

    assert replayed == created
    assert reopened.get_human_review(task_id) == created
    assert reopened.get_task(task_id).snapshot == created.after
    assert created.before == before
    assert created.submission == body
    assert created.after.runtime_status == "completed"
    assert created.after.inspection_result == "normal"
    assert created.reviewed_at.tzinfo is not None

    with pytest.raises(SubmissionConflict):
        store.apply_human_review(
            task_id,
            HumanReviewSubmission.model_validate(body.model_dump() | {"note": "changed"}),
        )
    with pytest.raises(SubmissionConflict):
        store.apply_human_review(task_id, submission(before, request_id=uuid4()))
    assert store.get_human_review(task_id) == created
    assert store.get_task(task_id).snapshot == created.after


def test_request_id_is_globally_unique_even_when_content_matches(repository):
    store, _, _ = repository
    first_id, first = create_waiting_task(store)
    second_id, second = create_waiting_task(store)
    request_id = uuid4()
    first_body = submission(first, request_id=request_id)
    store.apply_human_review(first_id, first_body)
    second_body = HumanReviewSubmission.model_validate(
        first_body.model_dump()
        | {
            "snapshot_updated_at": second.updated_at,
            "decisions": (
                first_body.decisions[0].model_dump()
                | {"finding_id": second.findings[0].finding_id},
            ),
        }
    )

    with pytest.raises(SubmissionConflict):
        store.apply_human_review(second_id, second_body)
    assert store.get_human_review(second_id) is None
    assert store.get_task(second_id).snapshot == second


def test_concurrent_submissions_apply_one_batch_once(repository):
    store, settings, schema = repository
    task_id, snapshot = create_waiting_task(store)
    first = submission(snapshot)
    second = submission(snapshot)
    barrier = Barrier(2)

    def apply_once(body):
        candidate = HumanReviewRepository(settings.database_dsn, schema=schema)
        barrier.wait(timeout=10)
        try:
            return candidate.apply_human_review(task_id, body)
        except SubmissionConflict as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(apply_once, (first, second)))

    records = [result for result in results if not isinstance(result, Exception)]
    conflicts = [result for result in results if isinstance(result, SubmissionConflict)]
    assert len(records) == len(conflicts) == 1
    assert store.get_human_review(task_id) == records[0]
    assert store.get_task(task_id).snapshot == records[0].after
