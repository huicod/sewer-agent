"""Bounded enhancement invocation and original-coordinate provenance, with no detector/VLM."""

import io
import math
from typing import Protocol

from PIL import Image

from pipe_inspector.contracts.assets import AssetRecord
from pipe_inspector.contracts.common import ImageRegion
from pipe_inspector.contracts.enhancement import EnhancementRequest, EnhancementResult
from pipe_inspector.media import MediaStore
from pipe_inspector.vision.quality import analyze_quality

ENHANCEMENT_MODELS = {
    "dehaze": ("dehazeformer-mct", "dehazeformer-mct-8e0b26c7.pth"),
    "deblur": ("restormer-motion-deblurring", "restormer-motion-v1.0.pth"),
    "super_resolution": ("realesr-general-x4v3", "realesr-general-x4v3-v0.2.5.0.pth"),
}


class EnhancementAdapter(Protocol):
    kind: str
    model_id: str
    model_version: str
    scale: int

    def enhance_image(self, image: Image.Image) -> Image.Image: ...

    def unload(self) -> None: ...


class EnhancementError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(f"Enhancement unavailable: {code}")


def _png(image: Image.Image) -> io.BytesIO:
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    stream.seek(0)
    return stream


def run_enhancement(
    request: EnhancementRequest,
    media: MediaStore,
    adapter: EnhancementAdapter,
    *,
    max_input_pixels: int = 1_048_576,
    max_output_pixels: int = 4_194_304,
) -> EnhancementResult:
    """Always write a new asset; caller owns checkpoint/DB admission in the online layer."""
    if adapter.kind != request.kind:
        raise EnhancementError("model_kind_mismatch")
    if (
        type(adapter.scale) is not int
        or adapter.scale < 1
        or (request.kind != "super_resolution" and adapter.scale != 1)
        or (request.kind == "super_resolution" and adapter.scale <= 1)
        or not isinstance(adapter.model_id, str)
        or not adapter.model_id.strip()
        or not isinstance(adapter.model_version, str)
        or not adapter.model_version.strip()
    ):
        raise EnhancementError("invalid_model_metadata")
    with Image.open(media.path_for(request.input_asset)) as source:
        if source.width * source.height > media.max_pixels:
            raise EnhancementError("source_pixel_limit")
        if request.roi is not None:
            if (request.roi.width, request.roi.height) != source.size:
                raise EnhancementError("roi_source_mismatch")
            x1, y1, x2, y2 = request.roi.bbox_xyxy
            box = (math.floor(x1), math.floor(y1), math.ceil(x2), math.ceil(y2))
        else:
            box = (0, 0, source.width, source.height)
        width, height = box[2] - box[0], box[3] - box[1]
        if width * height > max_input_pixels:
            raise EnhancementError("input_pixel_limit")
        expected = (width * adapter.scale, height * adapter.scale)
        if expected[0] * expected[1] > min(max_output_pixels, media.max_pixels):
            raise EnhancementError("output_pixel_limit")
        region = ImageRegion(width=source.width, height=source.height, bbox_xyxy=box)
        image = source.crop(box).convert("RGB")
    quality_before = analyze_quality(_png(image))
    try:
        output = adapter.enhance_image(image)
        if not isinstance(output, Image.Image) or output.mode != "RGB" or output.size != expected:
            raise EnhancementError("invalid_model_output")
        stream = _png(output)
        quality_after = analyze_quality(stream)
        stream.seek(0)
    finally:
        adapter.unload()
    ingested = media.ingest(stream)
    derived = AssetRecord.model_validate(
        ingested.model_dump() | {"kind": "enhanced_image", "parent": request.input_asset}
    )
    return EnhancementResult(
        input_asset=request.input_asset,
        output_asset=derived,
        kind=request.kind,
        model_id=adapter.model_id,
        model_version=adapter.model_version,
        source_region=region,
        quality_before=quality_before,
        quality_after=quality_after,
    )


def map_enhanced_region(
    result: EnhancementResult, bbox_xyxy: tuple[float, float, float, float]
) -> ImageRegion:
    """Map a region of the derived view back to its original image, without rounding."""
    output = result.output_asset
    region = ImageRegion(width=output.width, height=output.height, bbox_xyxy=bbox_xyxy)
    x1, y1, x2, y2 = result.source_region.bbox_xyxy
    sx, sy = (x2 - x1) / output.width, (y2 - y1) / output.height
    bx1, by1, bx2, by2 = region.bbox_xyxy
    return ImageRegion(
        width=result.source_region.width,
        height=result.source_region.height,
        bbox_xyxy=(x1 + bx1 * sx, y1 + by1 * sy, x1 + bx2 * sx, y1 + by2 * sy),
    )
