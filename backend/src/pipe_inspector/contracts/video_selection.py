"""Bounded scene-screening and detailed-review selection contracts."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from .common import ContractModel, NonEmptyStr

VIDEO_SELECTION_VERSION = "video-selection-v1"
FrameIndex = Annotated[int, Field(ge=0)]
SceneReason = Annotated[str, Field(min_length=1, max_length=300)]


class VideoSceneAssessment(ContractModel):
    frame_index: FrameIndex
    scene: Literal["pipe", "outside", "unassessable", "unknown"]
    reason: SceneReason


class VideoSceneBatch(ContractModel):
    assessments: tuple[VideoSceneAssessment, ...] = Field(max_length=16)


class VideoReviewSelection(ContractModel):
    version: Literal["video-selection-v1"] = "video-selection-v1"
    candidate_count: int = Field(ge=0)
    pool_frames: tuple[FrameIndex, ...] = Field(max_length=64)
    review_frames: tuple[FrameIndex, ...]
    deferred_count: int = Field(ge=0)
    unreviewed_event_count: int = Field(ge=0)
    unreviewed_audit_count: int = Field(ge=0)
    scene_calls: int = Field(default=0, ge=0, le=4)
    scene_errors: tuple[NonEmptyStr, ...] = ()
    assessments: tuple[VideoSceneAssessment, ...] = ()

    @model_validator(mode="after")
    def consistent_selection(self) -> "VideoReviewSelection":
        pool = set(self.pool_frames)
        reviewed = set(self.review_frames)
        assessed = [item.frame_index for item in self.assessments]
        if len(pool) != len(self.pool_frames):
            raise ValueError("pool frame indices must be unique")
        if len(reviewed) != len(self.review_frames):
            raise ValueError("review frame indices must be unique")
        if len(assessed) != len(set(assessed)):
            raise ValueError("assessment frame indices must be unique")
        if not reviewed.issubset(pool):
            raise ValueError("review frames must belong to the candidate pool")
        if not set(assessed).issubset(pool):
            raise ValueError("assessments must belong to the candidate pool")
        if self.candidate_count < len(pool):
            raise ValueError("candidate count cannot be smaller than the pool")
        if self.deferred_count != self.candidate_count - len(reviewed):
            raise ValueError("deferred count must describe candidates without detailed review")
        if len(self.scene_errors) > self.scene_calls:
            raise ValueError("scene errors cannot exceed scene calls")
        return self
