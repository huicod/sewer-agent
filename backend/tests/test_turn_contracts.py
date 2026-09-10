from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pipe_inspector.contracts.turns import TurnInput, TurnRecord, TurnSubmission
from pydantic import ValidationError


def submission_data(**changes):
    return {
        "request_id": uuid4(),
        "session_id": uuid4(),
        "text": "Inspect the attached image",
    } | changes


def record_data(**changes):
    now = datetime(2026, 9, 10, tzinfo=timezone.utc)
    return submission_data() | {"created_at": now, "updated_at": now} | changes


def test_turn_models_preserve_submission_and_defaults_through_json_roundtrip():
    task_id = uuid4()
    asset_id = uuid4()
    submission = TurnSubmission.model_validate(
        submission_data(
            input_asset={"asset_id": asset_id},
            task_id=task_id,
        )
    )
    record = TurnRecord.model_validate(
        submission.model_dump()
        | {
            "created_at": datetime(2026, 9, 10, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 9, 10, tzinfo=timezone.utc),
        }
    )

    restored = TurnRecord.model_validate_json(record.model_dump_json())

    assert restored.request_id == submission.request_id
    assert restored.session_id == submission.session_id
    assert restored.input_asset == submission.input_asset
    assert restored.task_id == task_id
    assert restored.status == "queued"
    assert restored.stage == "queued"
    assert restored.reply is None
    assert restored.linked_task_id is None
    assert restored.error_code is None
    assert restored.attempts == 0


@pytest.mark.parametrize(
    "model,data",
    [
        (TurnInput, {"request_id": uuid4(), "text": ""}),
        (TurnInput, {"request_id": uuid4(), "text": "x" * 4001}),
        (TurnInput, {"request_id": uuid4(), "text": "ok", "path": "/etc/passwd"}),
        (TurnSubmission, submission_data(session_id="not-a-uuid")),
        (TurnRecord, record_data(attempts=-1)),
        (TurnRecord, record_data(reply="x" * 4001)),
        (TurnRecord, record_data(error_code="x" * 101)),
        (TurnRecord, record_data(created_at=datetime(2026, 9, 10))),
    ],
)
def test_turn_models_reject_invalid_bounds_ids_extra_fields_and_naive_times(model, data):
    with pytest.raises(ValidationError):
        model.model_validate(data)


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "completed", "stage": "finished"},
        {"status": "completed", "stage": "finished", "reply": "   "},
        {"status": "completed", "stage": "executing", "reply": "done"},
        {"status": "failed", "stage": "finished"},
        {"status": "failed", "stage": "finished", "error_code": "   "},
        {"status": "failed", "stage": "executing", "error_code": "timeout"},
        {"status": "queued", "error_code": "timeout"},
        {"status": "running", "error_code": "timeout"},
    ],
)
def test_turn_record_rejects_inconsistent_terminal_and_error_states(changes):
    with pytest.raises(ValidationError):
        TurnRecord.model_validate(record_data(**changes))


def test_terminal_turn_states_accept_required_payloads():
    completed = TurnRecord.model_validate(
        record_data(status="completed", stage="finished", reply="Inspection complete")
    )
    failed = TurnRecord.model_validate(
        record_data(status="failed", stage="finished", error_code="workflow_failed")
    )

    assert completed.reply == "Inspection complete"
    assert failed.error_code == "workflow_failed"
