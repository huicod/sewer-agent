from types import SimpleNamespace
from uuid import uuid4

import pytest
from pipe_inspector.config import Settings
from pipe_inspector.contracts.assets import AssetRecord, AssetRef
from pipe_inspector.contracts.common import ImageRegion
from pipe_inspector.contracts.geometry import GeometryOptions
from pipe_inspector.workflow.start import InspectionStartError, run_config, video_run_config


def source(**changes):
    return AssetRecord.model_validate(
        {
            "ref": AssetRef(asset_id=uuid4()),
            "kind": "image",
            "media_type": "image/png",
            "width": 8,
            "height": 6,
            "byte_size": 80,
            **changes,
        }
    )


def test_geometry_admission_requires_original_region_and_local_model(tmp_path):
    from pipe_inspector.workflow.geometry_config import (
        build_geometry_config,
        validate_geometry_input,
    )

    asset = source()
    with pytest.raises(InspectionStartError, match="区域"):
        validate_geometry_input(asset, GeometryOptions(operation="segment"))
    options = GeometryOptions(operation="relative_measure", roi_xyxy=(2, 1, 7, 5))
    assert validate_geometry_input(asset, options).bbox_xyxy == (2, 1, 7, 5)
    settings = Settings(_env_file=None, geometry_models_dir=tmp_path)
    with pytest.raises(InspectionStartError, match="部署"):
        build_geometry_config(settings, options)
    config = build_geometry_config(settings, options, check_files=False)
    assert config["segment"]["model_id"] == "facebook/sam2.1-hiera-large"
    assert config["depth"]["model_id"] == "depth-anything/Depth-Anything-V2-Small-hf"
    assert config["segment"]["call_id"] != config["depth"]["call_id"]


@pytest.mark.parametrize("invalid", ["outside", "enhanced", "large", "frame"])
def test_geometry_bad_source_fails_before_model_execution(invalid):
    from pipe_inspector.workflow.geometry_config import validate_geometry_input

    changes = {}
    if invalid in {"enhanced", "frame"}:
        changes = {
            "kind": "frame" if invalid == "frame" else "enhanced_image",
            "parent": AssetRef(asset_id=uuid4()),
        }
    if invalid == "large":
        changes = {"width": 4096, "height": 4096}
    options = GeometryOptions(
        operation="segment", roi_xyxy=(2, 1, 9 if invalid == "outside" else 7, 5)
    )
    with pytest.raises(InspectionStartError):
        validate_geometry_input(source(**changes), options)


def test_finding_selector_must_belong_to_same_task_and_original_source():
    from pipe_inspector.workflow.geometry_config import validate_geometry_input

    asset = source()
    finding_id = uuid4()
    region = ImageRegion(width=8, height=6, bbox_xyxy=(2, 1, 7, 5))
    finding = SimpleNamespace(
        finding_id=finding_id, evidence=[SimpleNamespace(asset=asset.ref, region=region)]
    )
    task = SimpleNamespace(input_asset=asset.ref, snapshot=SimpleNamespace(findings=[finding]))
    options = GeometryOptions(operation="segment", finding_id=finding_id)
    assert validate_geometry_input(asset, options, task) == region
    with pytest.raises(InspectionStartError):
        validate_geometry_input(asset, options)
    with pytest.raises(InspectionStartError):
        validate_geometry_input(
            asset, GeometryOptions(operation="segment", finding_id=uuid4()), task
        )
    finding.evidence[0].asset = AssetRef(asset_id=uuid4())
    with pytest.raises(InspectionStartError):
        validate_geometry_input(asset, options, task)


def test_depth_only_admits_no_mask_and_geometry_video_is_rejected():
    from pipe_inspector.agent.intent import compile_inspection_plan
    from pipe_inspector.contracts.conversation import ConversationIntent
    from pipe_inspector.contracts.planning import ActionKind
    from pipe_inspector.workflow.geometry_config import validate_geometry_input

    options = GeometryOptions(operation="relative_depth")
    assert validate_geometry_input(source(), options) is None
    plan = compile_inspection_plan(
        ConversationIntent(
            kind="inspect", actions=("relative_depth",), message="深度图", geometry=options
        ),
        available_actions=set(ActionKind),
    )
    with pytest.raises(InspectionStartError) as caught:
        video_run_config(Settings(_env_file=None), plan=plan)
    assert caught.value.code == "geometry_image_only"


def test_direct_plan_cannot_silently_skip_unconfigured_geometry():
    from pipe_inspector.contracts.planning import TaskPlan

    plan = TaskPlan(
        steps=(
            {"step_id": "detect", "action": "detect"},
            {"step_id": "segment", "action": "segment", "depends_on": ("detect",)},
        )
    )
    with pytest.raises(InspectionStartError) as caught:
        run_config(Settings(_env_file=None), plan=plan)
    assert caught.value.code == "geometry_parameters_required"
