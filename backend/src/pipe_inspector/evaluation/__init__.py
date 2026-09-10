"""Bounded, offline-recomputable evaluation helpers."""

from .review import EvaluationManifest, LabeledBox, evaluate_manifest, score_boxes, summarize

__all__ = [
    "EvaluationManifest",
    "LabeledBox",
    "evaluate_manifest",
    "score_boxes",
    "summarize",
]
