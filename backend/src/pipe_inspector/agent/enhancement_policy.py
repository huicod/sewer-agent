"""Provisional suggestions, not calibrated blur/haze diagnoses or automatic actions."""

from pipe_inspector.contracts.common import ImageRegion
from pipe_inspector.contracts.enhancement import EnhancementDecision, EnhancementKind
from pipe_inspector.contracts.inspection import QualitySummary

ENHANCEMENT_POLICY_VERSION = "enhancement-gate-v1"


def choose_enhancement(
    quality: QualitySummary,
    *,
    requested: EnhancementKind | str | None = None,
    roi: ImageRegion | None = None,
) -> EnhancementDecision:
    if requested is not None:
        kind = EnhancementKind(requested)
        if kind == EnhancementKind.SUPER_RESOLUTION and roi is None:
            return EnhancementDecision(kind=kind, reason="roi_required")
        return EnhancementDecision(kind=kind, should_run=True, reason="explicit_request")
    if {"too_dark", "too_bright"} & set(quality.reasons):
        return EnhancementDecision(reason="lighting_not_supported")
    if quality.adequate:
        return EnhancementDecision(reason="quality_adequate")
    kind = (
        EnhancementKind.DEBLUR
        if "blurred" in quality.reasons
        else EnhancementKind.DEHAZE
        if "low_contrast" in quality.reasons
        else None
    )
    return EnhancementDecision(kind=kind, reason="automatic_disabled")
