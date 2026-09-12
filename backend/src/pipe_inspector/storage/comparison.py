"""Atomic conversation-turn storage for immutable comparison snapshots."""

from uuid import UUID

from psycopg import sql
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from pipe_inspector.comparison import build_comparison
from pipe_inspector.contracts.assets import AssetRecord
from pipe_inspector.contracts.comparison import ComparisonResult
from pipe_inspector.contracts.intake import TaskRecord
from pipe_inspector.contracts.turns import TurnRecord, TurnSubmission
from pipe_inspector.storage.repository import MissingRecord, SubmissionConflict


def _validate_turn_bindings(
    submission: TurnSubmission,
    request_id: UUID,
    reference_task_id: UUID,
    current_task_id: UUID,
) -> None:
    reference_binding = getattr(submission, "reference_task_id", None)
    if (
        submission.request_id != request_id
        or submission.input_asset is not None
        or (reference_binding is not None and reference_binding != reference_task_id)
        or (submission.task_id is not None and submission.task_id != current_task_id)
    ):
        raise SubmissionConflict


def _validate_cached(
    payload: dict,
    submission: TurnSubmission,
    request_id: UUID,
    reference_task_id: UUID,
    current_task_id: UUID,
) -> None:
    try:
        result = ComparisonResult.model_validate(payload)
    except (TypeError, ValidationError):
        raise SubmissionConflict from None
    _validate_turn_bindings(
        submission, request_id, reference_task_id, current_task_id
    )
    if (
        result.request_id != request_id
        or result.reference.snapshot.task_id != reference_task_id
        or result.current.snapshot.task_id != current_task_id
        or result.reference.session_id != submission.session_id
        or result.current.session_id != submission.session_id
    ):
        raise SubmissionConflict


def _load_scoped_task(repository, connection, task_id: UUID, session_id: UUID):
    row = connection.execute(
        sql.SQL(
            "SELECT record FROM {} WHERE task_id = %s AND session_id = %s"
        ).format(repository._table("sewer_tasks")),
        (task_id, session_id),
    ).fetchone()
    if row is None:
        raise MissingRecord
    try:
        task = TaskRecord.model_validate(row[0])
    except ValidationError:
        raise SubmissionConflict from None
    if task.snapshot.task_id != task_id or task.session_id != session_id:
        raise SubmissionConflict
    return task


def _load_asset(repository, connection, task: TaskRecord) -> AssetRecord:
    row = connection.execute(
        sql.SQL(
            "SELECT record FROM {} WHERE asset_id = %s AND version = %s"
        ).format(repository._table("sewer_assets")),
        (task.input_asset.asset_id, task.input_asset.version),
    ).fetchone()
    if row is None:
        raise MissingRecord
    try:
        asset = AssetRecord.model_validate(row[0])
    except ValidationError:
        raise SubmissionConflict from None
    if asset.ref != task.input_asset:
        raise SubmissionConflict
    return asset


def save_comparison(
    repository,
    request_id: UUID,
    reference_task_id: UUID,
    current_task_id: UUID,
    *,
    attempt: int = 1,
) -> dict:
    """Save or replay one exact comparison snapshot under a locked turn."""

    with repository._connect() as connection:
        row = connection.execute(
            sql.SQL(
                "SELECT submission, record, checkpoints, session_id FROM {} "
                "WHERE request_id = %s FOR UPDATE"
            ).format(repository._table("sewer_conversation_turns")),
            (request_id,),
        ).fetchone()
        if row is None:
            raise MissingRecord
        try:
            submission = TurnSubmission.model_validate(row[0])
            turn = TurnRecord.model_validate(row[1])
        except ValidationError:
            raise SubmissionConflict from None
        checkpoints = row[2]
        if (
            submission.request_id != request_id
            or turn.request_id != request_id
            or submission.session_id != row[3]
            or turn.session_id != row[3]
            or not isinstance(checkpoints, dict)
        ):
            raise SubmissionConflict

        if "comparison" in checkpoints:
            cached = checkpoints["comparison"]
            _validate_cached(
                cached,
                submission,
                request_id,
                reference_task_id,
                current_task_id,
            )
            return cached

        if turn.status != "running":
            raise SubmissionConflict

        _validate_turn_bindings(
            submission, request_id, reference_task_id, current_task_id
        )
        reference = _load_scoped_task(
            repository, connection, reference_task_id, turn.session_id
        )
        current = _load_scoped_task(
            repository, connection, current_task_id, turn.session_id
        )
        reference_media = _load_asset(repository, connection, reference)
        current_media = _load_asset(repository, connection, current)
        result = build_comparison(
            request_id,
            reference,
            current,
            reference_media,
            current_media,
            attempt=attempt,
        )
        payload = result.model_dump(mode="json")
        checkpoints["comparison"] = payload
        connection.execute(
            sql.SQL(
                "UPDATE {} SET checkpoints = %s WHERE request_id = %s"
            ).format(repository._table("sewer_conversation_turns")),
            (Jsonb(checkpoints), request_id),
        )
    return payload
