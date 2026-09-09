"""Behavioral contracts for constrained task plans and tool-call records."""

import math
from uuid import UUID

import pytest
from pipe_inspector.contracts.detection import DetectionLabel
from pipe_inspector.contracts.planning import ActionKind, OutputFormat, TaskPlan
from pipe_inspector.contracts.tools import ToolCall, ToolCallStatus
from pydantic import ValidationError

TASK_ID = "11111111-1111-1111-1111-111111111111"
CALL_ID = "22222222-2222-2222-2222-222222222222"
ASSET_ID = "33333333-3333-3333-3333-333333333333"


def valid_plan() -> dict:
    return {
        "schema_version": "1",
        "target_labels": ["PL", "FS"],
        "open_anomaly_audit": True,
        "steps": [
            {"step_id": "detect-all", "action": "detect"},
            {"step_id": "segment-pl", "action": "segment", "depends_on": ["detect-all"]},
            {
                "step_id": "verify-mask",
                "action": "verify",
                "depends_on": ["segment-pl"],
            },
            {
                "step_id": "relative-size",
                "action": "relative_measure",
                "depends_on": ["verify-mask"],
            },
        ],
        "outputs": ["json", "html"],
        "budget": {"max_tool_calls": 4},
    }


def valid_tool_call() -> dict:
    return {
        "call_id": CALL_ID,
        "task_id": TASK_ID,
        "step_id": "verify-1",
        "idempotency_key": "task:verify-1",
        "tool_name": "verify",
        "tool_version": "vlm-review-v1",
        "resource": "api",
        "timeout_seconds": 30.0,
        "input_assets": [{"asset_id": ASSET_ID, "version": 1}],
    }


def test_valid_plan_supports_focused_reporting_and_json_roundtrip() -> None:
    plan = TaskPlan.model_validate(valid_plan())

    assert plan.target_labels == (DetectionLabel.PL, DetectionLabel.FS)
    assert plan.steps[0].action is ActionKind.DETECT
    assert plan.outputs == (OutputFormat.JSON, OutputFormat.HTML)
    assert TaskPlan.model_validate_json(plan.model_dump_json()) == plan


def test_default_target_labels_cover_exactly_the_five_detector_classes() -> None:
    data = valid_plan()
    data.pop("target_labels")

    plan = TaskPlan.model_validate(data)

    assert set(plan.target_labels) == set(DetectionLabel)
    assert len(plan.target_labels) == 5


@pytest.mark.parametrize("field", ["target_labels", "outputs"])
def test_plan_rejects_empty_or_duplicate_selection_fields(field: str) -> None:
    empty = valid_plan()
    empty[field] = []
    duplicate = valid_plan()
    duplicate[field] = [duplicate[field][0], duplicate[field][0]]

    with pytest.raises(ValidationError):
        TaskPlan.model_validate(empty)
    with pytest.raises(ValidationError):
        TaskPlan.model_validate(duplicate)


@pytest.mark.parametrize(
    "steps",
    [
        [],
        [{"step_id": "verify", "action": "verify"}],
        [
            {"step_id": "detect-1", "action": "detect"},
            {"step_id": "detect-2", "action": "detect", "depends_on": ["detect-1"]},
        ],
        [
            {"step_id": "detect", "action": "detect", "required": False},
            {"step_id": "verify", "action": "verify", "depends_on": ["detect"]},
        ],
        [
            {"step_id": "detect", "action": "detect", "depends_on": ["later"]},
            {"step_id": "later", "action": "verify", "depends_on": ["detect"]},
        ],
    ],
)
def test_plan_requires_one_required_dependency_free_detect_at_position_zero(
    steps: list[dict],
) -> None:
    data = valid_plan()
    data["steps"] = steps
    data["budget"] = {"max_tool_calls": max(1, len(steps))}

    with pytest.raises(ValidationError):
        TaskPlan.model_validate(data)


@pytest.mark.parametrize("extra_field", ["command", "path", "parameters"])
def test_plan_step_rejects_loose_execution_fields(extra_field: str) -> None:
    data = valid_plan()
    data["steps"][1][extra_field] = "unsafe"

    with pytest.raises(ValidationError):
        TaskPlan.model_validate(data)


def test_plan_rejects_unknown_action() -> None:
    data = valid_plan()
    data["steps"][1]["action"] = "execute_shell"

    with pytest.raises(ValidationError):
        TaskPlan.model_validate(data)


@pytest.mark.parametrize(
    "steps",
    [
        [
            {"step_id": "detect", "action": "detect"},
            {"step_id": "verify", "action": "verify", "depends_on": ["later"]},
            {"step_id": "later", "action": "summarize", "depends_on": ["detect"]},
        ],
        [
            {"step_id": "detect", "action": "detect"},
            {"step_id": "verify", "action": "verify", "depends_on": ["verify"]},
        ],
        [
            {"step_id": "detect", "action": "detect"},
            {"step_id": "first", "action": "verify", "depends_on": ["second"]},
            {"step_id": "second", "action": "summarize", "depends_on": ["first"]},
        ],
        [
            {"step_id": "detect", "action": "detect"},
            {"step_id": "verify", "action": "verify", "depends_on": ["missing"]},
        ],
        [
            {"step_id": "detect", "action": "detect"},
            {
                "step_id": "verify",
                "action": "verify",
                "depends_on": ["detect", "detect"],
            },
        ],
    ],
)
def test_plan_rejects_forward_self_cyclic_dangling_or_duplicate_dependencies(
    steps: list[dict],
) -> None:
    data = valid_plan()
    data["steps"] = steps
    data["budget"] = {"max_tool_calls": len(steps)}

    with pytest.raises(ValidationError):
        TaskPlan.model_validate(data)


def test_plan_rejects_duplicate_step_ids() -> None:
    data = valid_plan()
    data["steps"][2]["step_id"] = "segment-pl"

    with pytest.raises(ValidationError):
        TaskPlan.model_validate(data)


def test_every_non_detect_step_needs_a_dependency_path_to_detect() -> None:
    data = valid_plan()
    data["steps"][1]["depends_on"] = []

    with pytest.raises(ValidationError):
        TaskPlan.model_validate(data)


def test_relative_measure_requires_segment_ancestor() -> None:
    data = valid_plan()
    data["steps"] = [
        {"step_id": "detect", "action": "detect"},
        {"step_id": "verify", "action": "verify", "depends_on": ["detect"]},
        {
            "step_id": "relative-size",
            "action": "relative_measure",
            "depends_on": ["verify"],
        },
    ]
    data["budget"] = {"max_tool_calls": 3}

    with pytest.raises(ValidationError):
        TaskPlan.model_validate(data)


def test_plan_rejects_more_steps_than_budget() -> None:
    data = valid_plan()
    data["budget"] = {"max_tool_calls": 3}

    with pytest.raises(ValidationError):
        TaskPlan.model_validate(data)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_tool_calls", 0),
        ("max_tool_calls", 129),
        ("max_transport_attempts", 0),
        ("max_transport_attempts", 6),
        ("per_event_supplement", 2),
        ("per_event_open_scan", -1),
        ("per_event_verifications", 3),
    ],
)
def test_call_budget_enforces_limits(field: str, value: int) -> None:
    data = valid_plan()
    data["budget"] = {field: value}

    with pytest.raises(ValidationError):
        TaskPlan.model_validate(data)


def test_failed_tool_call_has_safe_structured_error_and_roundtrips() -> None:
    data = valid_tool_call()
    data.update(
        {
            "status": "failed",
            "error": {
                "code": "authentication_failed",
                "retryable": False,
                "message": "Provider authentication failed",
            },
        }
    )

    call = ToolCall.model_validate(data)

    assert call.status is ToolCallStatus.FAILED
    assert call.error is not None and call.error.retryable is False
    assert ToolCall.model_validate_json(call.model_dump_json()) == call


def test_uncertain_tool_call_preserves_partial_outputs_and_retry_metadata() -> None:
    data = valid_tool_call()
    data.update(
        {
            "attempt": 2,
            "status": "uncertain",
            "output_assets": [{"asset_id": ASSET_ID, "version": 2}],
            "error": {
                "code": "completion_unknown",
                "retryable": True,
                "message": "Completion could not be confirmed",
            },
        }
    )

    call = ToolCall.model_validate(data)

    assert call.attempt == 2
    assert call.error is not None and call.error.retryable is True
    assert call.output_assets[0].version == 2


def test_skipped_tool_call_requires_reason_without_error() -> None:
    data = valid_tool_call()
    data.update({"status": "skipped", "skip_reason": "Scale source is unavailable"})

    call = ToolCall.model_validate(data)

    assert call.status is ToolCallStatus.SKIPPED
    assert call.skip_reason == "Scale source is unavailable"
    assert call.error is None


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "failed"},
        {"status": "uncertain"},
        {
            "status": "succeeded",
            "error": {"code": "bad", "retryable": False, "message": "Unexpected error"},
        },
        {
            "status": "queued",
            "error": {"code": "bad", "retryable": False, "message": "Unexpected error"},
        },
        {
            "status": "running",
            "error": {"code": "bad", "retryable": False, "message": "Unexpected error"},
        },
        {"status": "skipped"},
        {"status": "skipped", "skip_reason": " "},
        {
            "status": "skipped",
            "skip_reason": "Not needed",
            "error": {"code": "bad", "retryable": False, "message": "Unexpected error"},
        },
        {
            "status": "failed",
            "skip_reason": "Not needed",
            "error": {"code": "failed", "retryable": False, "message": "Tool failed"},
        },
    ],
)
def test_tool_call_rejects_invalid_status_error_or_skip_reason_combinations(
    changes: dict,
) -> None:
    data = valid_tool_call()
    data.update(changes)

    with pytest.raises(ValidationError):
        ToolCall.model_validate(data)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("attempt", 0),
        ("timeout_seconds", 0),
        ("timeout_seconds", -1),
        ("timeout_seconds", math.nan),
        ("timeout_seconds", math.inf),
    ],
)
def test_tool_call_rejects_invalid_attempt_or_timeout(field: str, value: float) -> None:
    data = valid_tool_call()
    data[field] = value

    with pytest.raises(ValidationError):
        ToolCall.model_validate(data)


def test_tool_call_parses_ids_and_asset_references() -> None:
    call = ToolCall.model_validate(valid_tool_call())

    assert call.call_id == UUID(CALL_ID)
    assert call.task_id == UUID(TASK_ID)
    assert call.input_assets[0].asset_id == UUID(ASSET_ID)
