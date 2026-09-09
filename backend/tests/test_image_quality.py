from PIL import Image
from pipe_inspector.vision.quality import QUALITY_POLICY_VERSION, analyze_quality


def test_quality_accepts_clear_high_contrast_image(tmp_path) -> None:
    path = tmp_path / "clear.png"
    image = Image.new("L", (64, 64))
    image.putdata([255 if ((x // 4) + (y // 4)) % 2 else 0 for y in range(64) for x in range(64)])
    image.save(path)

    quality = analyze_quality(path)

    assert QUALITY_POLICY_VERSION == "image-quality-v1"
    assert 120 <= quality.mean_luminance <= 135
    assert quality.contrast >= 100
    assert quality.sharpness > 0
    assert quality.adequate is True
    assert quality.reasons == ()


def test_quality_reports_dark_low_information_image(tmp_path) -> None:
    path = tmp_path / "dark.png"
    Image.new("L", (32, 32), color=0).save(path)

    quality = analyze_quality(path)

    assert quality.adequate is False
    assert {"too_dark", "low_contrast", "blurred"}.issubset(quality.reasons)
