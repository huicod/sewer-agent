"""Video initial-scan values; candidates and coverage are not a final inspection verdict."""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from .assets import AssetRef
from .common import ContractModel, ImageRegion, NonEmptyStr
from .detection import DetectionLabel
from .inspection import ImageObservation, QualitySummary


class FrameObservation(ImageObservation):
    frame_index: int = Field(ge=0)
    timestamp_seconds: float = Field(ge=0)


class VideoFrameToolPin(ContractModel):
    version: Literal["video-frame-tool-v1"] = "video-frame-tool-v1"
    parent_task_id: UUID
    video_asset: AssetRef
    frame_asset: AssetRef
    frame_index: int = Field(ge=0)
    timestamp_seconds: float = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    operation: Literal["enhance", "segment", "relative_depth", "relative_measure"]

    @model_validator(mode="after")
    def distinct_source_assets(self):
        if self.frame_asset == self.video_asset:
            raise ValueError("frame asset must be derived from its video")
        return self


class FrameLocator(ContractModel):
    frame_index: int = Field(ge=0)
    timestamp_seconds: float = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    quality: QualitySummary


class EventEvidence(FrameLocator):
    detection_index: int | None = Field(default=None, ge=0)
    region: ImageRegion
    score: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def matching_dimensions(self):
        if (self.width, self.height) != (self.region.width, self.region.height):
            raise ValueError("evidence dimensions differ from its original-pixel region")
        return self


class VideoEvent(ContractModel):
    event_id: NonEmptyStr
    label: DetectionLabel
    start_frame: int = Field(ge=0)
    end_frame: int = Field(ge=0)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)
    observations: int = Field(ge=1)
    last_region: ImageRegion
    evidence: tuple[EventEvidence, ...] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def ordered_interval(self):
        if self.end_frame < self.start_frame or self.end_seconds < self.start_seconds:
            raise ValueError("event interval must be ordered")
        if self.observations > self.end_frame - self.start_frame + 1:
            raise ValueError("event cannot have multiple observations in one frame")
        if any(
            not self.start_frame <= item.frame_index <= self.end_frame for item in self.evidence
        ):
            raise ValueError("evidence must belong to the event interval")
        return self


class VideoAggregationConfig(ContractModel):
    policy_version: str = "video-events-v1"
    iou_threshold: float = Field(default=0.3, gt=0, le=1)
    max_gap_frames: int = Field(default=3, ge=0)
    max_gap_seconds: float = Field(default=1.0, gt=0)
    evidence_separation_seconds: float = Field(default=0.25, ge=0)
    audit_interval_seconds: float = Field(default=10, gt=0)


class VideoAggregationState(ContractModel):
    source: AssetRef
    config: VideoAggregationConfig = VideoAggregationConfig()
    detector_id: str | None = None
    model_version: str | None = None
    label_space_version: str | None = None
    next_frame: int = Field(default=0, ge=0)
    last_timestamp_seconds: float | None = Field(default=None, ge=0)
    adequate_frames: int = Field(default=0, ge=0)
    active: tuple[VideoEvent, ...] = ()
    events: tuple[VideoEvent, ...] = ()
    audit_frames: tuple[FrameLocator, ...] = ()
    finalized: bool = False

    @model_validator(mode="after")
    def consistent_progress(self):
        if self.adequate_frames > self.next_frame:
            raise ValueError("quality coverage exceeds processed frames")
        if self.next_frame and (
            self.last_timestamp_seconds is None
            or not all((self.detector_id, self.model_version, self.label_space_version))
        ):
            raise ValueError("processed state requires timestamp and detector pins")
        if self.finalized and self.active:
            raise ValueError("finalized state cannot contain active events")
        ids = [item.event_id for item in (*self.events, *self.active)]
        if len(ids) != len(set(ids)):
            raise ValueError("event IDs must be unique")
        return self


class VideoScanResult(ContractModel):
    state: VideoAggregationState
    decode_complete: bool
    error_code: NonEmptyStr | None = None
    inspection_result: Literal["pending"] = "pending"

    @model_validator(mode="after")
    def truthful_coverage(self):
        if self.decode_complete and (self.error_code is not None or self.state.next_frame == 0):
            raise ValueError("complete coverage requires decoded frames and no error")
        return self


class VideoEvidenceArtifact(FrameLocator):
    relative_path: NonEmptyStr
