from uuid import uuid4

import pytest
from pipe_inspector.agent.intent import IntentPlanningError, compile_inspection_plan
from pipe_inspector.contracts.conversation import ConversationIntent
from pipe_inspector.contracts.planning import ActionKind, TaskPlan


def test_explicit_enhancement_plan_runs_after_original_review_and_retains_options():
    intent = ConversationIntent.model_validate(
        {
            "kind": "inspect",
            "actions": ["enhance"],
            "message": "去雾后辅助复核",
            "enhancement": {"kind": "dehaze", "roi_xyxy": None},
        }
    )
    plan = compile_inspection_plan(intent, available_actions=set(ActionKind))
    assert [s.action for s in plan.steps] == [
        "detect",
        "verify",
        "enhance",
        "summarize",
        "export_report",
    ]
    assert plan.enhancement.kind == "dehaze"
    assert plan.steps[2].depends_on == ("verify",)


def test_legacy_intent_without_kind_is_readable_but_not_executable():
    intent = ConversationIntent(kind="inspect", actions=("enhance",), message="增强")
    with pytest.raises(IntentPlanningError) as error:
        compile_inspection_plan(intent, available_actions=set(ActionKind))
    assert error.value.code == "enhancement_parameters_required"


@pytest.mark.parametrize(
    "options",
    [
        {"kind": "execute_shell"},
        {"kind": "deblur", "checkpoint": "/private/model.pth"},
        {"kind": "deblur", "roi_xyxy": [3, 1, 2, 4]},
        {"kind": "deblur", "roi_xyxy": [-1, 0, 2, 4]},
    ],
)
def test_enhancement_options_reject_executable_or_invalid_coordinates(options):
    from pipe_inspector.contracts.enhancement import EnhancementOptions

    with pytest.raises(ValueError):
        EnhancementOptions.model_validate(options)


def test_options_without_enhance_action_are_rejected_by_intent_and_plan():
    with pytest.raises(ValueError):
        ConversationIntent(kind="ask", message="查询", enhancement={"kind": "dehaze"})
    with pytest.raises(ValueError):
        TaskPlan(steps=({"step_id": "detect", "action": "detect"},), enhancement={"kind": "dehaze"})


def test_auxiliary_review_limits_duplicate_references_and_unbounded_anomalies():
    from pipe_inspector.contracts.enhancement import EnhancementReview

    finding_id = uuid4()
    with pytest.raises(ValueError):
        EnhancementReview(
            quality_effect="improved",
            summary="边缘变化",
            conflicting_finding_ids=(finding_id, finding_id),
        )
    with pytest.raises(ValueError):
        EnhancementReview(
            quality_effect="improved",
            summary="边缘变化",
            additional_anomalies=[{"label": "PL", "reason": "裂纹"}] * 13,
        )


def test_enhancement_pins_are_server_owned_and_missing_roi_or_models_fail(tmp_path):
    from pipe_inspector.config import Settings
    from pipe_inspector.contracts.enhancement import EnhancementOptions
    from pipe_inspector.workflow.enhancement_config import build_enhancement_config
    from pipe_inspector.workflow.start import InspectionStartError

    settings = Settings(_env_file=None, enhancement_models_dir=tmp_path)
    options = EnhancementOptions(kind="dehaze")
    with pytest.raises(InspectionStartError) as missing:
        build_enhancement_config(settings, options)
    assert missing.value.code == "enhancement_unavailable"
    (tmp_path / "dehazeformer-mct-8e0b26c7.pth").write_bytes(b"fixture")
    pin = build_enhancement_config(settings, options)
    assert pin["model_id"] == "dehazeformer-mct"
    assert pin["model_version"] == "dehazeformer-mct-8e0b26c7.pth"
    assert pin["options"] == {"kind": "dehaze", "roi_xyxy": None}
    with pytest.raises(InspectionStartError) as no_roi:
        build_enhancement_config(settings, EnhancementOptions(kind="super_resolution"))
    assert no_roi.value.code == "enhancement_roi_required"


def test_video_plan_cannot_silently_skip_enhancement(tmp_path):
    from pipe_inspector.config import Settings
    from pipe_inspector.workflow.start import InspectionStartError, video_run_config

    intent = ConversationIntent(
        kind="inspect", actions=("enhance",), message="去雾", enhancement={"kind": "dehaze"}
    )
    plan = compile_inspection_plan(intent, available_actions=set(ActionKind))
    with pytest.raises(InspectionStartError) as error:
        video_run_config(Settings(_env_file=None), plan=plan)
    assert error.value.code == "enhancement_image_only"
