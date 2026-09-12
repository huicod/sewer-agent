from uuid import uuid4

import pytest
from pipe_inspector.agent.intent import compile_inspection_plan
from pipe_inspector.contracts.conversation import ConversationIntent
from pipe_inspector.contracts.planning import ActionKind, TaskPlan


def intent(operation="segment", **options):
    return ConversationIntent.model_validate(
        {
            "kind": "inspect",
            "actions": [operation],
            "message": "request",
            "geometry": {"operation": operation, **options},
        }
    )


@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        ("segment", ["detect", "verify", "segment", "summarize", "export_report"]),
        ("relative_depth", ["detect", "verify", "relative_depth", "summarize", "export_report"]),
        (
            "relative_measure",
            ["detect", "verify", "segment", "relative_measure", "summarize", "export_report"],
        ),
    ],
)
def test_geometry_plan_runs_after_original_review(operation, expected):
    options = {} if operation == "relative_depth" else {"roi_xyxy": [2, 1, 7, 5]}
    plan = compile_inspection_plan(intent(operation, **options), available_actions=set(ActionKind))
    assert [step.action.value for step in plan.steps] == expected
    assert plan.geometry.operation == operation
    assert all(step.required for step in plan.steps)


def test_missing_segment_region_can_be_parsed_but_not_silently_invented():
    from pipe_inspector.agent.intent import IntentPlanningError

    with pytest.raises(IntentPlanningError, match="geometry_region_required"):
        compile_inspection_plan(intent(), available_actions=set(ActionKind))


def test_options_cannot_add_unrequested_tool_or_mix_enhancement():
    payload = intent(roi_xyxy=[2, 1, 7, 5]).model_dump(mode="json")
    with pytest.raises(ValueError):
        ConversationIntent.model_validate(payload | {"actions": ["verify"]})
    with pytest.raises(ValueError):
        ConversationIntent.model_validate(
            payload
            | {
                "actions": ["segment", "enhance"],
                "enhancement": {"kind": "deblur"},
            }
        )


@pytest.mark.parametrize(
    "options",
    [
        {"roi_xyxy": [2, 1, 7, 5], "finding_id": str(uuid4())},
        {"roi_xyxy": [7, 1, 2, 5]},
        {"roi_xyxy": [-1, 1, 2, 5]},
        {"roi_xyxy": [2, 1, float("nan"), 5]},
        {"checkpoint": "/arbitrary/model"},
    ],
)
def test_geometry_options_reject_ambiguous_or_invalid_parameters(options):
    from pipe_inspector.contracts.geometry import GeometryOptions

    with pytest.raises(ValueError):
        GeometryOptions(operation="segment", **options)


def test_old_plan_still_loads_without_new_commands():
    plan = TaskPlan.model_validate({"steps": [{"step_id": "detect", "action": "detect"}]})
    assert plan.geometry is None
    assert len(plan.steps) == 1


async def test_parser_request_explains_measurement_package_instead_of_rejecting_its_steps():
    import json
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from pipe_inspector.agent.intent import IntentParser

    client = SimpleNamespace(
        generate=AsyncMock(return_value=intent("relative_measure", roi_xyxy=[280, 66, 549, 350]))
    )
    await IntentParser(client).parse("分割并做相对量化")
    prompt = client.generate.call_args.args[0][0]["content"]
    assert "relative_measure 本身包含分割、深度和统计" in prompt
    assert "不因提到这些组成步骤而澄清" in prompt
    example = '{"kind":"inspect","actions":["verify","relative_measure"]'
    assert example in prompt
    assert json.loads(prompt.split("response_schema: ")[1])["properties"]["geometry"]


@pytest.mark.parametrize("kind", ["inspect", "supplement"])
async def test_geometry_dialogue_creates_correct_new_or_linked_task(monkeypatch, kind):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from pipe_inspector.agent.conversation import ConversationDispatcher
    from pipe_inspector.config import Settings
    from pipe_inspector.contracts.turns import TurnRecord
    from pipe_inspector.workflow import geometry_config
    from test_conversation_dispatch import Store

    store = Store()
    build = geometry_config.build_geometry_config
    monkeypatch.setattr(geometry_config, "build_geometry_config", lambda s, o: build(s, o, False))
    starter = SimpleNamespace(
        settings=Settings(_env_file=None),
        start=AsyncMock(),
        admit_supplement=AsyncMock(return_value=store.task),
    )
    turn = TurnRecord.model_validate(
        store.turn.model_dump()
        | {
            "input_asset": store.asset,
            "task_id": store.task.snapshot.task_id if kind == "supplement" else None,
        }
    )
    parsed = ConversationIntent.model_validate(
        intent(roi_xyxy=[2, 1, 7, 5]).model_dump() | {"kind": kind}
    )
    result = await ConversationDispatcher(store, starter).handle(turn, parsed)
    assert "已启动" in result["reply"]
    assert result["linked_task_id"] == str(store.task.snapshot.task_id)
    starter.start.assert_awaited_once()
    if kind == "inspect":
        assert len(store.created) == 1
        assert starter.start.call_args.kwargs["plan"].geometry.operation == "segment"
    else:
        assert not store.created
        assert starter.admit_supplement.call_args.kwargs["geometry"].roi_xyxy == (2, 1, 7, 5)
        assert "VLM" in result["reply"]


async def test_region_clarification_does_not_create_or_execute_task():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from pipe_inspector.agent.conversation import ConversationDispatcher
    from test_conversation_dispatch import Store

    store = Store()
    starter = SimpleNamespace(start=AsyncMock())
    result = await ConversationDispatcher(store, starter).handle(store.turn, intent())
    assert "区域" in result["reply"]
    assert not store.created
    starter.start.assert_not_awaited()


async def test_existing_task_geometry_cannot_bypass_linked_limits_via_inspect_intent():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from pipe_inspector.agent.conversation import ConversationDispatcher
    from pipe_inspector.contracts.turns import TurnRecord
    from test_conversation_dispatch import Store

    store = Store()
    turn = TurnRecord.model_validate(
        store.turn.model_dump() | {"task_id": store.task.snapshot.task_id}
    )
    starter = SimpleNamespace(start=AsyncMock(), admit_supplement=AsyncMock())
    dispatcher = ConversationDispatcher(store, starter)
    dispatcher._preflight_geometry = lambda *args: None
    result = await dispatcher.handle(turn, intent(roi_xyxy=[2, 1, 7, 5]))
    assert "关联" in result["reply"] and "本次未启动" in result["reply"]
    assert not store.created
    starter.start.assert_not_awaited()
    starter.admit_supplement.assert_not_awaited()


async def test_geometry_supplement_reuses_admission_before_model_preflight(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from pipe_inspector.config import Settings
    from pipe_inspector.contracts.geometry import GeometryOptions
    from pipe_inspector.workflow.start import InspectionStarter
    from test_enhancement_start_recovery import _child, _submission

    submission, parent_id = _submission(), uuid4()
    existing = _child(submission, parent_id)
    repo = SimpleNamespace(
        find_task_by_request_id=Mock(return_value=existing),
        load_step=Mock(side_effect=AssertionError("must reuse frozen admission")),
    )
    result = await InspectionStarter(
        Settings(_env_file=None, geometry_models_dir=tmp_path), repo
    ).admit_supplement(parent_id, submission, geometry=GeometryOptions(operation="relative_depth"))
    assert result == existing
    repo.load_step.assert_not_called()


async def test_asking_about_standalone_depth_reads_saved_evidence_without_a_tool_call():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from pipe_inspector.agent.conversation import ConversationDispatcher
    from pipe_inspector.contracts.turns import TurnRecord
    from test_geometry_api import geometry_fixture

    task, _, depth, _, _ = geometry_fixture()
    turn = TurnRecord(
        request_id=uuid4(),
        session_id=task.session_id,
        task_id=task.snapshot.task_id,
        text="解释深度结果",
        created_at=task.created_at,
        updated_at=task.created_at,
    )
    repo = SimpleNamespace(
        get_task=lambda _: task,
        load_step=lambda _, step: (
            {"result": depth.model_dump(mode="json"), "measurements": None}
            if step == "geometry_depth"
            else None
        ),
    )
    starter = SimpleNamespace(start=AsyncMock())
    response = await ConversationDispatcher(repo, starter).handle(
        turn, ConversationIntent(kind="ask", message="query")
    )
    assert "白近黑远" in response["reply"]
    assert "不能换算真实尺寸" in response["reply"]
    starter.start.assert_not_awaited()
