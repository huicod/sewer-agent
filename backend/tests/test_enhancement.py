import io
from uuid import uuid4

import pytest
from PIL import Image
from pipe_inspector.contracts.assets import AssetRef
from pipe_inspector.contracts.common import ImageRegion
from pipe_inspector.contracts.inspection import QualitySummary
from pipe_inspector.media import MediaStore


@pytest.fixture
def source(tmp_path):
    media = MediaStore(tmp_path / "assets", max_pixels=100_000)
    image = Image.new("RGB", (32, 24), (80, 120, 160))
    image.putpixel((8, 8), (255, 0, 0))
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    stream.seek(0)
    asset = media.ingest(stream)
    return media, asset


class Model:
    """Small deterministic stand-in; real pretrained models have separate smoke tests."""

    model_id = "fixture"
    model_version = "fixture-v1"

    def __init__(self, kind="deblur", scale=1, *, invalid=None):
        self.kind, self.scale, self.invalid = kind, scale, invalid
        self.calls = self.unloads = 0

    def enhance_image(self, image):
        self.calls += 1
        if self.invalid == "failure":
            raise RuntimeError("model failed")
        size = (image.width * self.scale, image.height * self.scale)
        if self.invalid == "size":
            size = (size[0] + 1, size[1])
        result = image.resize(size)
        return result.convert("L") if self.invalid == "mode" else result

    def unload(self):
        self.unloads += 1


def test_super_resolution_requires_an_explicit_roi():
    from pipe_inspector.contracts.enhancement import EnhancementRequest

    with pytest.raises(ValueError, match="ROI"):
        EnhancementRequest(input_asset=AssetRef(asset_id=uuid4()), kind="super_resolution")


@pytest.mark.parametrize(
    ("reasons", "kind", "reason"),
    [
        ((), None, "quality_adequate"),
        (("blurred",), "deblur", "automatic_disabled"),
        (("low_contrast",), "dehaze", "automatic_disabled"),
        (("too_dark", "blurred"), None, "lighting_not_supported"),
        (("too_bright",), None, "lighting_not_supported"),
    ],
)
def test_quality_gate_never_automatically_runs_unvalidated_enhancement(reasons, kind, reason):
    from pipe_inspector.agent.enhancement_policy import choose_enhancement

    quality = QualitySummary(
        mean_luminance=100, contrast=20, sharpness=60, adequate=not reasons, reasons=reasons
    )
    decision = choose_enhancement(quality)
    assert not decision.should_run
    assert decision.kind == kind
    assert decision.reason == reason


def test_explicit_request_is_distinct_from_provisional_quality_suggestion():
    from pipe_inspector.agent.enhancement_policy import choose_enhancement

    quality = QualitySummary(mean_luminance=100, contrast=20, sharpness=60, adequate=True)
    assert choose_enhancement(quality, requested="deblur").should_run
    assert not choose_enhancement(quality, requested="super_resolution").should_run
    roi = ImageRegion(width=32, height=24, bbox_xyxy=(2, 3, 10, 12))
    decision = choose_enhancement(quality, requested="super_resolution", roi=roi)
    assert decision.should_run and decision.reason == "explicit_request"


def test_runner_preserves_original_and_records_derived_asset(source):
    from pipe_inspector.contracts.enhancement import EnhancementRequest
    from pipe_inspector.vision.enhancement import run_enhancement

    media, asset = source
    original = media.path_for(asset.ref).read_bytes()
    model = Model()
    result = run_enhancement(
        EnhancementRequest(input_asset=asset.ref, kind="deblur"), media, model
    )
    assert media.path_for(asset.ref).read_bytes() == original
    assert result.output_asset.ref != asset.ref
    assert result.output_asset.kind == "enhanced_image"
    assert result.output_asset.parent == asset.ref
    assert result.model_id == "fixture" and result.model_version == "fixture-v1"
    assert (result.output_asset.width, result.output_asset.height) == (32, 24)
    assert result.source_region.bbox_xyxy == (0, 0, 32, 24)
    assert result.review_required
    assert model.calls == model.unloads == 1


def test_fractional_roi_uses_actual_crop_and_maps_output_to_original(source):
    from pipe_inspector.contracts.enhancement import EnhancementRequest
    from pipe_inspector.vision.enhancement import map_enhanced_region, run_enhancement

    media, asset = source
    result = run_enhancement(
        EnhancementRequest(
            input_asset=asset.ref,
            kind="super_resolution",
            roi=ImageRegion(width=32, height=24, bbox_xyxy=(2.4, 3.7, 10.2, 12.1)),
        ),
        media,
        Model("super_resolution", scale=4),
    )
    assert result.source_region.bbox_xyxy == (2, 3, 11, 13)
    assert (result.output_asset.width, result.output_asset.height) == (36, 40)
    mapped = map_enhanced_region(result, (4, 8, 20, 24))
    assert mapped.bbox_xyxy == (3, 5, 7, 9)
    assert (mapped.width, mapped.height) == (32, 24)
    with pytest.raises(ValueError):
        map_enhanced_region(result, (0, 0, 37, 40))


@pytest.mark.parametrize("invalid", ["size", "mode", "failure"])
def test_invalid_model_outputs_do_not_publish_assets_and_always_unload(source, invalid):
    from pipe_inspector.contracts.enhancement import EnhancementRequest
    from pipe_inspector.vision.enhancement import EnhancementError, run_enhancement

    media, asset = source
    paths = set(media.root.rglob("source"))
    model = Model(invalid=invalid)
    with pytest.raises((EnhancementError, RuntimeError)):
        run_enhancement(EnhancementRequest(input_asset=asset.ref, kind="deblur"), media, model)
    assert set(media.root.rglob("source")) == paths
    assert model.unloads == 1


@pytest.mark.parametrize("which", ["input", "output", "roi", "kind"])
def test_invalid_requests_are_rejected_before_model_inference(source, which):
    from pipe_inspector.contracts.enhancement import EnhancementRequest
    from pipe_inspector.vision.enhancement import EnhancementError, run_enhancement

    media, asset = source
    options = {}
    roi = None
    if which == "input":
        options["max_input_pixels"] = 100
    if which == "output":
        options["max_output_pixels"] = 100
    if which == "roi":
        roi = ImageRegion(width=40, height=30, bbox_xyxy=(2, 2, 4, 4))
    model = Model("dehaze" if which == "kind" else "deblur")
    with pytest.raises(EnhancementError):
        run_enhancement(
            EnhancementRequest(input_asset=asset.ref, kind="deblur", roi=roi),
            media,
            model,
            **options,
        )
    assert model.calls == 0
