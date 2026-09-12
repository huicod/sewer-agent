"""Server-owned local model pins and explicit image-only enhancement admission."""

import math
from uuid import uuid4

from pipe_inspector.agent.enhancement_review import ENHANCEMENT_REVIEW_VERSION
from pipe_inspector.contracts.common import ImageRegion
from pipe_inspector.contracts.enhancement import EnhancementOptions, EnhancementSelection
from pipe_inspector.vision.enhancement import ENHANCEMENT_MODELS
from pipe_inspector.workflow.start import InspectionStartError

ENHANCEMENT_ONLINE_VERSION = "enhancement-online-v1"
ENHANCEMENT_TIMEOUT = 180


def build_enhancement_config(settings, options: EnhancementOptions, *, check_files=True) -> dict:
    if options.kind == "super_resolution" and options.roi_xyxy is None:
        raise InspectionStartError(
            "enhancement_roi_required", "局部超分需要明确的原图像素框 [x1,y1,x2,y2]", 409
        )
    return _model_pin(settings, options.kind, options.model_dump(mode="json"), check_files)


def _model_pin(settings, kind, options, check_files):
    model_id, filename = ENHANCEMENT_MODELS[kind]
    checkpoint = (settings.enhancement_models_dir / filename).resolve()
    if check_files and not checkpoint.is_file():
        raise InspectionStartError("enhancement_unavailable", "所选增强模型未部署", 409)
    return {
        "version": ENHANCEMENT_ONLINE_VERSION,
        "options": options,
        "model_id": model_id,
        "model_version": filename,
        "checkpoint": str(checkpoint),
        "device": settings.enhancement_device,
        "call_id": str(uuid4()),
        "review_call_id": str(uuid4()),
        "review_prompt_version": ENHANCEMENT_REVIEW_VERSION,
    }


def build_auto_enhancement_config(settings):
    from pipe_inspector.agent.enhancement_selection import SELECTION_VERSION

    models = {}
    for kind in ENHANCEMENT_MODELS:
        try:
            models[kind] = _model_pin(settings, kind, None, True)
        except InspectionStartError:
            continue
    return {"version": SELECTION_VERSION, "call_id": str(uuid4()), "models": models}


def selected_enhancement_pin(config, selection):
    """Derive execution solely from immutable candidates and a durable selection."""
    from pipe_inspector.agent.enhancement_selection import SELECTION_VERSION

    selection = EnhancementSelection.model_validate(selection)
    auto = config["auto_enhancement"]
    if auto["version"] != SELECTION_VERSION:
        raise ValueError("selection version changed")
    if selection.status != "selected":
        return None
    options = selection.options
    candidate = auto["models"][options.kind]
    return candidate | {"options": options.model_dump(mode="json")}


def validate_enhancement_input(asset, options: EnhancementOptions) -> None:
    if asset.kind != "image" or asset.parent is not None:
        raise InspectionStartError("enhancement_image_only", "当前增强只支持原始图片任务", 409)
    if options.kind == "super_resolution" and options.roi_xyxy is None:
        raise InspectionStartError("enhancement_roi_required", "请提供局部超分的原图像素框", 409)
    box = options.roi_xyxy or (0, 0, asset.width, asset.height)
    try:
        ImageRegion(width=asset.width, height=asset.height, bbox_xyxy=box)
    except ValueError:
        raise InspectionStartError("enhancement_roi_invalid", "增强区域超出原图或坐标无效", 409)
    width = math.ceil(box[2]) - math.floor(box[0])
    height = math.ceil(box[3]) - math.floor(box[1])
    scale = 4 if options.kind == "super_resolution" else 1
    if width * height > 1_048_576 or width * height * scale**2 > 4_194_304:
        raise InspectionStartError(
            "enhancement_pixel_limit", "所选区域超过增强像素预算，请指定更小的原图 ROI", 409
        )
