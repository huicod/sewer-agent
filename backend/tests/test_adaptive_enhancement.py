import json
from uuid import uuid4

import httpx
import pytest
from pipe_inspector.agent.intent import compile_inspection_plan
from pipe_inspector.contracts.conversation import ConversationIntent
from pipe_inspector.contracts.planning import ActionKind
from pydantic import SecretStr


def auto_intent(**changes):
    return ConversationIntent.model_validate(
        dict(kind="inspect", actions=["enhance"], auto_enhancement=True, message="允许按需增强")
        | changes
    )


def test_auto_permission_compiles_one_conditional_step_after_original_review():
    plan = compile_inspection_plan(auto_intent(), available_actions=set(ActionKind))
    assert plan.auto_enhancement is True and plan.enhancement is None
    step = plan.steps[2]
    assert step.action == "enhance" and step.depends_on == ("verify",)
    assert step.required is False


def test_default_no_auto_permission_and_explicit_mode_is_mutually_exclusive():
    intent = ConversationIntent(kind="inspect", message="检查")
    assert intent.auto_enhancement is False
    with pytest.raises(ValueError):
        auto_intent(enhancement={"kind": "dehaze"})
    with pytest.raises(ValueError):
        auto_intent(kind="ask", actions=[])
    with pytest.raises(ValueError):
        auto_intent(actions=[])


def proposal(**changes):
    from pipe_inspector.contracts.enhancement import EnhancementProposal

    return EnhancementProposal.model_validate(
        dict(
            options={"kind": "low_light", "roi_xyxy": None},
            degradation="low_light",
            confidence="clear",
            evidence="左侧管壁暗部细节难以区分",
            inspection_impact="无法判读暗部裂纹候选的连续边缘",
        )
        | changes
    )


@pytest.mark.parametrize(
    ("changes", "available", "status", "reason"),
    [
        ({}, ["low_light"], "selected", "evidence_supported"),
        ({"confidence": "uncertain"}, ["low_light"], "skipped", "evidence_uncertain"),
        ({"degradation": "haze"}, ["low_light"], "skipped", "tool_degradation_mismatch"),
        ({"options": None, "degradation": "none"}, ["low_light"], "skipped", "not_needed"),
        ({}, ["dehaze"], "skipped", "model_unavailable"),
        (
            {"options": {"kind": "low_light", "roi_xyxy": [0, 0, 100, 100]}},
            ["low_light"],
            "skipped",
            "enhancement_roi_invalid",
        ),
        (
            {"options": {"kind": "super_resolution"}, "degradation": "small_detail"},
            ["super_resolution"],
            "skipped",
            "enhancement_roi_required",
        ),
    ],
)
def test_selection_gate_requires_evidence_allowed_model_and_valid_original_roi(
    changes, available, status, reason
):
    from pipe_inspector.agent.enhancement_selection import gate_proposal
    from pipe_inspector.contracts.assets import AssetRecord, AssetRef

    asset = AssetRecord(
        ref=AssetRef(asset_id=uuid4()),
        kind="image",
        width=32,
        height=24,
        media_type="image/png",
        byte_size=100,
    )
    decision = gate_proposal(proposal(**changes), asset, available_kinds=available)
    assert decision.status == status and decision.reason == reason
    assert (decision.options is not None) == (status == "selected")


@pytest.mark.asyncio
async def test_selector_sends_original_image_quality_and_baseline_not_a_new_defect_prompt(tmp_path):
    from PIL import Image
    from pipe_inspector.agent.api_client import OpenAICompatibleClient
    from pipe_inspector.agent.enhancement_selection import EnhancementSelector
    from pipe_inspector.contracts.inspection import InspectionDecision
    from pipe_inspector.contracts.tasks import CoverageSummary
    from pipe_inspector.vision.quality import analyze_quality

    path = tmp_path / "source.png"
    Image.new("RGB", (32, 24), (20, 20, 20)).save(path)
    captured = []

    def handler(request):
        captured.append(json.loads(request.read()))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": proposal().model_dump_json()}, "finish_reason": "stop"}
                ]
            },
        )

    selector = EnhancementSelector(
        OpenAICompatibleClient(
            "https://example.invalid/v1",
            "fixture",
            SecretStr("fixture"),
            transport=httpx.MockTransport(handler),
        )
    )
    baseline = InspectionDecision(
        findings=(),
        inspection_result="inconclusive",
        needs_review=True,
        reasons=(),
        coverage=CoverageSummary(),
    )
    result = await selector.propose(
        path,
        width=32,
        height=24,
        quality=analyze_quality(path),
        baseline=baseline,
        available_kinds=["low_light"],
    )
    assert result.options.kind == "low_light"
    content = captured[0]["messages"][1]["content"]
    assert len([x for x in content if x["type"] == "image_url"]) == 1
    metadata = json.loads(content[0]["text"])
    assert metadata["quality"]["mean_luminance"] == 20
    assert metadata["available_kinds"] == ["low_light"]
    assert metadata["baseline"]["inspection_result"] == "inconclusive"
    assert "options" in metadata["response_schema"]["properties"]


def test_auto_admission_freezes_available_models_and_never_enables_ordinary_tasks(tmp_path):
    from pipe_inspector.config import Settings
    from pipe_inspector.workflow.start import InspectionStartError, run_config, video_run_config

    settings = Settings(_env_file=None, enhancement_models_dir=tmp_path)
    (tmp_path / "dehazeformer-mct-8e0b26c7.pth").write_bytes(b"fixture")
    ordinary = run_config(settings)
    assert "auto_enhancement" not in ordinary and "enhancement" not in ordinary
    plan = compile_inspection_plan(auto_intent(), available_actions=set(ActionKind))
    run = run_config(settings, plan=plan)
    assert "enhancement" not in run
    assert set(run["auto_enhancement"]["models"]) == {"dehaze"}
    assert run["auto_enhancement"]["models"]["dehaze"]["options"] is None
    with pytest.raises(InspectionStartError) as error:
        video_run_config(settings, plan=plan)
    assert error.value.code == "enhancement_image_only"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["image", "video"])
async def test_auto_conversation_preflight_rejects_video_without_creating_task(tmp_path, kind):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from pipe_inspector.agent.conversation import ConversationDispatcher
    from pipe_inspector.config import Settings
    from test_conversation_dispatch import Store
    from test_enhancement_conversation import _turn_with_input

    store = Store(kind=kind)
    starter = SimpleNamespace(
        settings=Settings(_env_file=None, enhancement_models_dir=tmp_path), start=AsyncMock()
    )
    result = await ConversationDispatcher(store, starter).handle(
        _turn_with_input(store, "检查，允许按需增强"), auto_intent()
    )
    assert len(store.created) == (1 if kind == "image" else 0)
    if kind == "video":
        assert "视频" in result["reply"]
    else:
        assert starter.start.call_args.kwargs["plan"].auto_enhancement


@pytest.mark.asyncio
async def test_auto_supplement_is_not_silently_converted_to_explicit_enhancement():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from pipe_inspector.agent.conversation import ConversationDispatcher
    from test_conversation_dispatch import Store

    store = Store()
    starter = SimpleNamespace(admit_supplement=AsyncMock(), start=AsyncMock())
    reply = await ConversationDispatcher(store, starter).handle(
        store.turn, auto_intent(kind="supplement")
    )
    assert "新建图片检查" in reply["reply"]
    assert not store.created and starter.admit_supplement.call_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("selected", [False, True])
async def test_auto_workflow_only_executes_enhancement_after_selected_checkpoint(
    monkeypatch, selected
):
    from pipe_inspector.workflow.image import ImageInspectionWorkflow

    calls = []
    pin = {"options": {"kind": "low_light", "roi_xyxy": None}}

    async def execute(name, payload, **kwargs):
        calls.append((name, payload))
        if name == "select_image_enhancement":
            return {"enhancement": pin if selected else None}
        return {"runtime_status": "completed"} if name == "finish_image" else {}

    monkeypatch.setattr("pipe_inspector.workflow.image.workflow.execute_activity", execute)
    run = {"config": {"auto_enhancement": {}, "gpu_queue": "gpu", "api_timeout_seconds": 5}}
    await ImageInspectionWorkflow().run(run)
    assert [name for name, _ in calls] == [
        "publish_image_stage",
        "detect_image",
        "verify_image",
        "select_image_enhancement",
        *(
            ["prepare_image_enhancement", "enhance_image", "review_image_enhancement"]
            if selected
            else []
        ),
        "finish_image",
    ]
    assert "enhancement" not in run["config"]  # original admission remains immutable
    if selected:
        assert calls[-1][1]["run"]["config"]["enhancement"] == pin
