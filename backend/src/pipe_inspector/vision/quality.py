"""Versioned provisional image-quality gates for the primary still image."""

from pathlib import Path

from PIL import Image, ImageFilter, ImageStat

from pipe_inspector.contracts.inspection import QualitySummary

QUALITY_POLICY_VERSION = "image-quality-v1"
MIN_MEAN_LUMINANCE = 35.0
MAX_MEAN_LUMINANCE = 220.0
MIN_CONTRAST = 18.0
MIN_SHARPNESS = 50.0


def analyze_quality(path: Path) -> QualitySummary:
    with Image.open(path) as image:
        gray = image.convert("L")
        statistics = ImageStat.Stat(gray)
        mean_luminance = float(statistics.mean[0])
        contrast = float(statistics.stddev[0])
        if gray.width > 2 and gray.height > 2:
            edge_image = gray.filter(ImageFilter.FIND_EDGES).crop(
                (1, 1, gray.width - 1, gray.height - 1)
            )
            sharpness = float(ImageStat.Stat(edge_image).var[0])
        else:
            sharpness = 0.0

    reasons: list[str] = []
    if mean_luminance < MIN_MEAN_LUMINANCE:
        reasons.append("too_dark")
    if mean_luminance > MAX_MEAN_LUMINANCE:
        reasons.append("too_bright")
    if contrast < MIN_CONTRAST:
        reasons.append("low_contrast")
    if sharpness < MIN_SHARPNESS:
        reasons.append("blurred")
    return QualitySummary(
        mean_luminance=mean_luminance,
        contrast=contrast,
        sharpness=sharpness,
        adequate=not reasons,
        reasons=tuple(reasons),
    )
