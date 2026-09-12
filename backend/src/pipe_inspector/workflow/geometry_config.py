"""Server-owned local geometry model pins and explicit original-image admission."""

import json
from uuid import uuid4

from pipe_inspector.contracts.common import ImageRegion
from pipe_inspector.contracts.geometry import GeometryOptions
from pipe_inspector.vision.geometry import MAX_GEOMETRY_PIXELS
from pipe_inspector.vision.geometry_models import DEPTH_ANYTHING_V2_REVISION, SAM2_REVISION
from pipe_inspector.workflow.start import InspectionStartError

GEOMETRY_ONLINE_VERSION = "geometry-online-v1"
GEOMETRY_TIMEOUT = 180


def build_geometry_config(settings, options: GeometryOptions, check_files=True):
    models = {
        "segment": ("facebook/sam2.1-hiera-large", SAM2_REVISION, "sam2.1-hiera-large"),
        "depth": (
            "depth-anything/Depth-Anything-V2-Small-hf",
            DEPTH_ANYTHING_V2_REVISION,
            "depth-anything-v2-small",
        ),
    }
    config = {
        "version": GEOMETRY_ONLINE_VERSION,
        "options": options.model_dump(mode="json"),
        "segment": None,
        "depth": None,
    }
    needed = (
        ["segment"]
        if options.operation == "segment"
        else (["depth"] if options.operation == "relative_depth" else ["segment", "depth"])
    )
    for key in needed:
        model_id, version, folder = models[key]
        checkpoint = (settings.geometry_models_dir / folder).resolve()
        if check_files:
            try:
                files = ["model.safetensors", "config.json", "preprocessor_config.json"]
                if key == "segment":
                    files += ["processor_config.json", "video_preprocessor_config.json"]
                if not all((checkpoint / name).is_file() for name in files):
                    raise ValueError("incomplete checkpoint")
                provenance = json.loads((checkpoint / "provenance.json").read_text())
                if provenance != {"repo_id": model_id, "revision": version}:
                    raise ValueError("wrong model version")
            except (OSError, ValueError):
                raise InspectionStartError(
                    "geometry_unavailable", "所需几何模型未完整部署", 409
                ) from None
        config[key] = {
            "model_id": model_id,
            "model_version": version,
            "checkpoint": str(checkpoint),
            "device": settings.geometry_device,
            "call_id": str(uuid4()),
        }
    return config


def validate_geometry_input(asset, options: GeometryOptions, task=None):
    if asset.kind != "image" or asset.parent is not None:
        raise InspectionStartError("geometry_image_only", "几何工具当前仅支持原始图片", 409)
    if asset.width * asset.height > MAX_GEOMETRY_PIXELS:
        raise InspectionStartError("geometry_pixel_limit", "原图超过几何工具像素预算", 409)
    if options.operation == "relative_depth":
        return None
    if options.finding_id is not None:
        findings = (
            []
            if task is None or task.input_asset != asset.ref
            else [f for f in task.snapshot.findings if f.finding_id == options.finding_id]
        )
        regions = (
            []
            if len(findings) != 1
            else [
                e.region
                for e in findings[0].evidence
                if e.asset == asset.ref and e.region is not None
            ]
        )
        if len(regions) != 1:
            raise InspectionStartError(
                "geometry_finding_invalid", "请选择当前原图中有唯一定位框的 finding ID", 409
            )
        region = regions[0]
        if (region.width, region.height) != (asset.width, asset.height):
            raise InspectionStartError("geometry_roi_invalid", "发现区域尺寸与原图不一致", 409)
        return region
    if options.roi_xyxy is None:
        raise InspectionStartError(
            "geometry_region_required", "请提供分割区域：原图像素框或已有 finding ID", 409
        )
    try:
        return ImageRegion(width=asset.width, height=asset.height, bbox_xyxy=options.roi_xyxy)
    except ValueError:
        raise InspectionStartError(
            "geometry_roi_invalid", "分割区域超出原图或坐标无效", 409
        ) from None
