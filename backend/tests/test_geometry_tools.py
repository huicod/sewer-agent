import io
from uuid import uuid4

import numpy as np
import pytest
from PIL import Image
from pipe_inspector.contracts.assets import AssetRecord, AssetRef
from pipe_inspector.contracts.common import ImageRegion
from pipe_inspector.media import MediaStore


@pytest.fixture
def source(tmp_path):
    media = MediaStore(tmp_path / "assets")
    stream = io.BytesIO()
    Image.new("RGB", (8, 6), (40, 90, 130)).save(stream, format="PNG")
    stream.seek(0)
    return media, media.ingest(stream)


class Segmenter:
    model_id, model_version = "test-segmenter", "v1"

    def __init__(self, invalid=None):
        self.invalid, self.unloaded = invalid, False

    def segment_image(self, image, prompt):
        from pipe_inspector.vision.geometry import MaskPrediction

        assert image.size == (8, 6)
        assert prompt.bbox_xyxy == (2, 1, 7, 5)
        if self.invalid == "raises":
            raise RuntimeError("inference failed")
        mask = np.zeros((6, 8), dtype=bool)
        mask[2:4, 3:6] = True
        if self.invalid == "empty":
            mask[:] = False
        if self.invalid == "shape":
            mask = mask[:5]
        if self.invalid == "dtype":
            mask = mask.astype(float)
        return MaskPrediction(mask, float("nan") if self.invalid == "score" else 0.87)

    def unload(self):
        self.unloaded = True


class Depth:
    model_id, model_version, larger_is = "test-depth", "v1", "nearer"

    def __init__(self, invalid=None):
        self.invalid, self.unloaded = invalid, False

    def predict_depth(self, image):
        values = np.tile(np.arange(8, dtype=np.float32), (6, 1))
        if self.invalid == "shape":
            return values[:5]
        if self.invalid == "constant":
            return values * 0
        if self.invalid == "nan":
            values[0, 0] = np.nan
        return values

    def unload(self):
        self.unloaded = True


def _segment(media, asset, adapter=None):
    from pipe_inspector.contracts.geometry import SegmentationRequest
    from pipe_inspector.vision.geometry import run_segmentation

    request = SegmentationRequest(
        input_asset=asset.ref,
        prompt=ImageRegion(width=8, height=6, bbox_xyxy=(2, 1, 7, 5)),
    )
    return run_segmentation(request, asset, media, adapter or Segmenter())


def _depth(media, asset, adapter=None):
    from pipe_inspector.contracts.geometry import RelativeDepthRequest
    from pipe_inspector.vision.geometry import run_relative_depth

    return run_relative_depth(
        RelativeDepthRequest(input_asset=asset.ref), asset, media, adapter or Depth()
    )


def test_mask_is_real_binary_original_size_and_pixel_measurement_is_not_bbox_area(source):
    from pipe_inspector.vision.geometry import measure_geometry

    media, asset = source
    original = media.path_for(asset.ref).read_bytes()
    adapter = Segmenter()
    result = _segment(media, asset, adapter)
    assert adapter.unloaded
    assert media.path_for(asset.ref).read_bytes() == original
    assert result.mask_asset.parent == result.overlay_asset.parent == asset.ref
    assert result.mask_asset.ref != result.overlay_asset.ref != asset.ref
    with Image.open(media.path_for(result.mask_asset.ref)) as mask:
        assert mask.mode == "L" and mask.size == (8, 6)
        assert np.count_nonzero(np.asarray(mask)) == 6
        assert set(mask.getdata()) == {0, 255}
    measured = measure_geometry(result, media)
    assert measured.area_pixels == 6
    assert measured.image_plane_fraction == 0.125
    assert (measured.bbox_width_pixels, measured.bbox_height_pixels) == (3, 2)
    assert measured.metric_status == "unavailable_no_calibration"
    assert measured.relative_depth is None


def test_float_depth_roundtrips_and_measures_only_mask_values(source):
    from pipe_inspector.vision.geometry import measure_geometry

    media, asset = source
    adapter = Depth()
    depth = _depth(media, asset, adapter)
    assert adapter.unloaded
    assert depth.raw_asset.kind == depth.preview_asset.kind == "depth_map"
    assert depth.raw_asset.media_type == "application/x-npy"
    values = np.load(media.path_for(depth.raw_asset.ref), allow_pickle=False)
    assert values.dtype == np.float32 and values.shape == (6, 8)
    assert values[0].tolist() == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]
    assert depth.larger_is == "nearer"
    with Image.open(media.path_for(depth.preview_asset.ref)) as preview:
        assert preview.getpixel((0, 0)) == (0, 0, 0)
        assert preview.getpixel((7, 0)) == (255, 255, 255)
    measured = measure_geometry(_segment(media, asset), media, depth)
    assert measured.relative_depth.q10 == 3
    assert measured.relative_depth.median == 4
    assert measured.relative_depth.q90 == 5
    assert measured.relative_depth.units == "relative_model_units"


def test_farther_depth_preview_reverses_but_raw_values_remain_unchanged(source):
    media, asset = source
    adapter = Depth()
    adapter.larger_is = "farther"
    depth = _depth(media, asset, adapter)
    with Image.open(media.path_for(depth.preview_asset.ref)) as preview:
        assert preview.getpixel((0, 0)) == (255, 255, 255)
        assert preview.getpixel((7, 0)) == (0, 0, 0)


@pytest.mark.parametrize("invalid", ["empty", "shape", "dtype", "score", "raises"])
def test_invalid_mask_does_not_publish_evidence_and_unloads(source, invalid):
    media, asset = source
    adapter = Segmenter(invalid)
    before = set(media.root.rglob("source"))
    with pytest.raises((ValueError, RuntimeError)):
        _segment(media, asset, adapter)
    assert adapter.unloaded
    assert set(media.root.rglob("source")) == before


@pytest.mark.parametrize("invalid", ["shape", "constant", "nan"])
def test_invalid_depth_does_not_publish_evidence_and_unloads(source, invalid):
    media, asset = source
    adapter = Depth(invalid)
    before = set(media.root.rglob("source"))
    with pytest.raises(ValueError):
        _depth(media, asset, adapter)
    assert adapter.unloaded
    assert set(media.root.rglob("source")) == before


def test_enhanced_or_mismatched_source_cannot_be_measured(source):
    media, asset = source
    enhanced = AssetRecord.model_validate(
        asset.model_dump() | {"kind": "enhanced_image", "parent": AssetRef(asset_id=uuid4())}
    )
    with pytest.raises(ValueError, match="original"):
        _segment(media, enhanced)
    wrong_dimensions = AssetRecord.model_validate(asset.model_dump() | {"width": 9})
    with pytest.raises(ValueError, match="dimensions"):
        _depth(media, wrong_dimensions)


def test_measurement_rejects_cross_image_depth_and_forged_mask_metadata(source):
    from pipe_inspector.contracts.geometry import RelativeDepthResult
    from pipe_inspector.vision.geometry import measure_geometry

    media, asset = source
    mask, depth = _segment(media, asset), _depth(media, asset)
    unrelated = AssetRef(asset_id=uuid4())
    payload = depth.model_dump()
    payload["input_asset"] = unrelated.model_dump()
    for key in ("raw_asset", "preview_asset"):
        payload[key]["parent"] = unrelated.model_dump()
    other = RelativeDepthResult.model_validate(payload)
    with pytest.raises(ValueError, match="same source"):
        measure_geometry(mask, media, other)
    with Image.open(media.path_for(mask.mask_asset.ref)) as image:
        altered = image.copy()
    altered.putpixel((0, 0), 255)
    altered.save(media.path_for(mask.mask_asset.ref), format="PNG")
    with pytest.raises(ValueError, match="mask metadata"):
        measure_geometry(mask, media, depth)


def test_segment_request_requires_box_and_forbids_physical_units(source):
    from pipe_inspector.contracts.geometry import SegmentationRequest

    _, asset = source
    with pytest.raises(ValueError):
        SegmentationRequest(input_asset=asset.ref)
    with pytest.raises(ValueError):
        SegmentationRequest(
            input_asset=asset.ref,
            prompt=ImageRegion(width=8, height=6, bbox_xyxy=(2, 1, 7, 5)),
            units="mm",
        )


def test_mask_may_extend_beyond_prompt_without_silent_clipping(source):
    from pipe_inspector.vision.geometry import MaskPrediction, measure_geometry

    class Extended(Segmenter):
        def segment_image(self, image, prompt):
            prediction = super().segment_image(image, prompt)
            prediction.mask[0, 0] = True
            return MaskPrediction(prediction.mask, prediction.score)

    media, asset = source
    result = _segment(media, asset, Extended())
    assert result.mask_region.bbox_xyxy == (0, 0, 6, 4)
    assert measure_geometry(result, media).area_pixels == 7


@pytest.mark.parametrize("invalid", ["source_ref", "prompt_dimensions", "pixel_limit"])
def test_invalid_source_or_prompt_rejected_before_inference(source, invalid):
    from pipe_inspector.contracts.geometry import SegmentationRequest
    from pipe_inspector.vision.geometry import run_segmentation

    class NeverInfer(Segmenter):
        def segment_image(self, *args):
            pytest.fail("invalid input must not execute a model")

    media, asset = source
    ref = AssetRef(asset_id=uuid4()) if invalid == "source_ref" else asset.ref
    prompt = ImageRegion(
        width=9 if invalid == "prompt_dimensions" else 8, height=6, bbox_xyxy=(2, 1, 7, 5)
    )
    if invalid == "pixel_limit":
        media.max_pixels = 47
    adapter = NeverInfer()
    with pytest.raises(ValueError):
        run_segmentation(SegmentationRequest(input_asset=ref, prompt=prompt), asset, media, adapter)
    assert adapter.unloaded


def test_unknown_depth_direction_does_not_create_a_misleading_preview(source):
    media, asset = source
    adapter = Depth()
    adapter.larger_is = "unknown"
    with pytest.raises(ValueError, match="direction"):
        _depth(media, asset, adapter)
    assert adapter.unloaded
    assert len(list(media.root.rglob("source"))) == 1


def test_unload_failure_is_not_a_successful_tool_result(source):
    class CannotUnload(Segmenter):
        def unload(self):
            raise RuntimeError("cannot unload")

    media, asset = source
    with pytest.raises(RuntimeError, match="cannot unload"):
        _segment(media, asset, CannotUnload())
    assert len(list(media.root.rglob("source"))) == 1


@pytest.mark.parametrize("tool", ["segmentation", "depth"])
def test_inference_failure_survives_simultaneous_unload_failure(source, tool):
    from pipe_inspector.evaluation.geometry import _error

    class BothFail(Segmenter, Depth):
        def segment_image(self, *args):
            raise ValueError("inference failed")

        def predict_depth(self, *args):
            raise ValueError("inference failed")

        def unload(self):
            raise RuntimeError("cannot unload")

    media, asset = source
    operation = _segment if tool == "segmentation" else _depth
    with pytest.raises(ValueError, match="inference failed") as caught:
        operation(media, asset, BothFail())
    recorded = _error(caught.value)
    assert recorded["type"] == "ValueError"
    assert "inference failed" in recorded["message"]
    assert "cannot unload" in recorded["message"]
    assert len(list(media.root.rglob("source"))) == 1


def test_relative_depth_summary_requires_ordered_quantiles():
    from pipe_inspector.contracts.geometry import RelativeDepthSummary

    with pytest.raises(ValueError, match="quantile"):
        RelativeDepthSummary(q10=9, median=2, q90=-4, larger_is="nearer")


@pytest.mark.parametrize("invalid", ["parent", "alias", "dimensions", "metric"])
def test_persisted_geometry_contract_rejects_invalid_provenance(source, invalid):
    from pipe_inspector.contracts.geometry import RelativeDepthResult

    media, asset = source
    payload = _depth(media, asset).model_dump()
    if invalid == "parent":
        payload["raw_asset"]["parent"] = AssetRef(asset_id=uuid4()).model_dump()
    elif invalid == "alias":
        payload["raw_asset"]["ref"] = payload["preview_asset"]["ref"]
    elif invalid == "dimensions":
        payload["raw_asset"]["width"] = 9
    else:
        payload["units"] = "metres"
    with pytest.raises(ValueError):
        RelativeDepthResult.model_validate(payload)
