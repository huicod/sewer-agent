"""Prompted masks and relative depth on original image pixels, independent of verdicts."""

import io
import math
from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

import numpy as np
from PIL import Image

from pipe_inspector.contracts.assets import AssetRecord, AssetRef
from pipe_inspector.contracts.common import ImageRegion
from pipe_inspector.contracts.geometry import (
    DepthDirection,
    GeometryMeasurements,
    RelativeDepthRequest,
    RelativeDepthResult,
    RelativeDepthSummary,
    SegmentationRequest,
    SegmentationResult,
)
from pipe_inspector.media import MediaStore

MAX_GEOMETRY_PIXELS = 4_194_304


@dataclass(frozen=True)
class MaskPrediction:
    mask: np.ndarray
    score: float


class SegmenterAdapter(Protocol):
    model_id: str
    model_version: str

    def segment_image(self, image: Image.Image, prompt: ImageRegion) -> MaskPrediction: ...

    def unload(self) -> None: ...


class RelativeDepthAdapter(Protocol):
    model_id: str
    model_version: str
    larger_is: DepthDirection

    def predict_depth(self, image: Image.Image) -> np.ndarray: ...

    def unload(self) -> None: ...


def _source_image(ref: AssetRef, source: AssetRecord, media: MediaStore) -> Image.Image:
    if source.ref != ref or source.kind not in {"image", "frame"}:
        raise ValueError("geometry requires the matching original image or frame")
    if source.width * source.height > min(MAX_GEOMETRY_PIXELS, media.max_pixels):
        raise ValueError("geometry source pixel limit exceeded")
    with Image.open(media.path_for(ref)) as opened:
        if opened.size != (source.width, source.height):
            raise ValueError("source dimensions differ from metadata")
        return opened.convert("RGB")


def _model_metadata(adapter) -> None:
    if any(
        not isinstance(value, str) or not value.strip()
        for value in (adapter.model_id, adapter.model_version)
    ):
        raise ValueError("geometry model metadata is missing")


def _unload(adapter, primary_error: BaseException | None) -> None:
    try:
        adapter.unload()
    except Exception as error:
        if primary_error is None:
            raise
        primary_error.add_note(f"Model unload also failed: {type(error).__name__}: {error}")


def _save_png(image: Image.Image, source: AssetRef, kind: str, media: MediaStore) -> AssetRecord:
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    stream.seek(0)
    ingested = media.ingest(stream)
    return AssetRecord.model_validate(ingested.model_dump() | {"kind": kind, "parent": source})


def _mask_region(mask: np.ndarray) -> ImageRegion:
    ys, xs = np.nonzero(mask)
    if not len(xs):
        raise ValueError("empty mask is not measurement evidence")
    return ImageRegion(
        width=mask.shape[1],
        height=mask.shape[0],
        bbox_xyxy=(int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1),
    )


def run_segmentation(
    request: SegmentationRequest,
    source: AssetRecord,
    media: MediaStore,
    adapter: SegmenterAdapter,
) -> SegmentationResult:
    image = None
    primary_error = None
    try:
        _model_metadata(adapter)
        image = _source_image(request.input_asset, source, media)
        if image.size != (request.prompt.width, request.prompt.height):
            raise ValueError("prompt dimensions differ from original image")
        prediction = adapter.segment_image(image, request.prompt)
        if not isinstance(prediction, MaskPrediction) or (
            not isinstance(prediction.mask, np.ndarray)
            or prediction.mask.dtype != np.bool_
            or prediction.mask.shape != (image.height, image.width)
            or not math.isfinite(prediction.score)
        ):
            raise ValueError("invalid segmentation output")
        mask = prediction.mask.copy()
        region = _mask_region(mask)
        area = int(mask.sum())
        binary = Image.fromarray(mask.astype(np.uint8) * 255)
        overlay = np.asarray(image).copy()
        overlay[mask] = np.rint(overlay[mask] * 0.55 + np.array([40, 220, 120]) * 0.45)
        overlay_image = Image.fromarray(overlay)
    except BaseException as error:
        primary_error = error
        raise
    finally:
        if image is not None:
            image.close()
        _unload(adapter, primary_error)
    try:
        mask_asset = _save_png(binary, request.input_asset, "mask", media)
        overlay_asset = _save_png(overlay_image, request.input_asset, "mask", media)
    finally:
        binary.close()
        overlay_image.close()
    return SegmentationResult(
        input_asset=request.input_asset,
        prompt=request.prompt,
        mask_asset=mask_asset,
        overlay_asset=overlay_asset,
        model_id=adapter.model_id,
        model_version=adapter.model_version,
        model_score=prediction.score,
        area_pixels=area,
        mask_region=region,
    )


def _validate_depth(values: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    if (
        not isinstance(values, np.ndarray)
        or values.shape != (size[1], size[0])
        or (not np.issubdtype(values.dtype, np.floating) or not np.isfinite(values).all())
    ):
        raise ValueError("invalid depth shape or values")
    values = values.astype(np.float32)
    if not np.isfinite(values).all() or float(values.max()) <= float(values.min()):
        raise ValueError("depth has no finite relative range")
    return values


def _save_depth(values: np.ndarray, source: AssetRef, media: MediaStore) -> AssetRecord:
    # New private derived asset only; NPY is never accepted by the upload route.
    stream = io.BytesIO()
    np.save(stream, values, allow_pickle=False)
    payload = stream.getvalue()
    if len(payload) > media.max_upload_bytes:
        raise ValueError("depth asset byte limit exceeded")
    ref = AssetRef(asset_id=uuid4())
    path = media.path_for(ref)
    path.parent.mkdir(parents=True, exist_ok=False)
    try:
        with path.open("xb") as output:
            output.write(payload)
    except BaseException:
        path.unlink(missing_ok=True)
        path.parent.rmdir()
        path.parent.parent.rmdir()
        raise
    return AssetRecord(
        ref=ref,
        kind="depth_map",
        media_type="application/x-npy",
        width=values.shape[1],
        height=values.shape[0],
        byte_size=len(payload),
        parent=source,
    )


def run_relative_depth(
    request: RelativeDepthRequest,
    source: AssetRecord,
    media: MediaStore,
    adapter: RelativeDepthAdapter,
) -> RelativeDepthResult:
    image = None
    primary_error = None
    try:
        _model_metadata(adapter)
        if adapter.larger_is not in {"nearer", "farther"}:
            raise ValueError("relative depth direction is missing")
        image = _source_image(request.input_asset, source, media)
        values = _validate_depth(adapter.predict_depth(image), image.size)
        low, high = float(values.min()), float(values.max())
        normalized = (values.astype(np.float64) - low) / (high - low)
        if adapter.larger_is == "farther":
            normalized = 1 - normalized
        preview = Image.fromarray(np.rint(normalized * 255).astype(np.uint8)).convert("RGB")
    except BaseException as error:
        primary_error = error
        raise
    finally:
        if image is not None:
            image.close()
        _unload(adapter, primary_error)
    try:
        raw_asset = _save_depth(values, request.input_asset, media)
        preview_asset = _save_png(preview, request.input_asset, "depth_map", media)
    finally:
        preview.close()
    return RelativeDepthResult(
        input_asset=request.input_asset,
        raw_asset=raw_asset,
        preview_asset=preview_asset,
        model_id=adapter.model_id,
        model_version=adapter.model_version,
        larger_is=adapter.larger_is,
        raw_min=low,
        raw_max=high,
    )


def measure_geometry(
    segmentation: SegmentationResult,
    media: MediaStore,
    depth: RelativeDepthResult | None = None,
) -> GeometryMeasurements:
    asset = segmentation.mask_asset
    if depth is not None and (
        depth.input_asset != segmentation.input_asset
        or (depth.raw_asset.width, depth.raw_asset.height) != (asset.width, asset.height)
    ):
        raise ValueError("mask and depth must have the same source and dimensions")
    with Image.open(media.path_for(asset.ref)) as opened:
        if opened.mode != "L" or opened.size != (asset.width, asset.height):
            raise ValueError("invalid persisted mask metadata")
        values = np.asarray(opened).copy()
    if not np.isin(values, [0, 255]).all():
        raise ValueError("persisted mask is not binary")
    mask = values == 255
    region = _mask_region(mask)
    area = int(mask.sum())
    if area != segmentation.area_pixels or region != segmentation.mask_region:
        raise ValueError("persisted mask metadata does not match pixels")
    summary = None
    if depth is not None:
        values = _validate_depth(
            np.load(media.path_for(depth.raw_asset.ref), allow_pickle=False),
            (asset.width, asset.height),
        )
        if float(values.min()) != depth.raw_min or float(values.max()) != depth.raw_max:
            raise ValueError("persisted depth metadata does not match values")
        q10, median, q90 = np.quantile(values[mask], [0.1, 0.5, 0.9]).tolist()
        summary = RelativeDepthSummary(q10=q10, median=median, q90=q90, larger_is=depth.larger_is)
    x1, y1, x2, y2 = region.bbox_xyxy
    return GeometryMeasurements(
        input_asset=segmentation.input_asset,
        area_pixels=area,
        image_plane_fraction=area / (asset.width * asset.height),
        bbox_width_pixels=x2 - x1,
        bbox_height_pixels=y2 - y1,
        relative_depth=summary,
    )
