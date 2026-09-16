"""Compatibility exports for the shared guided fusion policy."""

from pipe_inspector.agent.guided_fusion import (
    ADDITION_DUPLICATE_IOU,
    FUSION_VERSION,
    NOVELTY_IOU,
    fuse_guided,
)

__all__ = ["ADDITION_DUPLICATE_IOU", "FUSION_VERSION", "NOVELTY_IOU", "fuse_guided"]
