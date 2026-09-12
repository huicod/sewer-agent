export type AssetRef = {
  asset_id: string;
  version: number;
};

export type AssetRecord = {
  ref: AssetRef;
  kind: "image" | "video" | string;
  media_type: string;
  byte_size: number;
  width: number | null;
  height: number | null;
  duration_seconds: number | null;
  parent: AssetRef | null;
};

export type RuntimeStatus =
  | "queued"
  | "running"
  | "waiting_review"
  | "completed"
  | "partial"
  | "failed"
  | "cancelled";

export type InspectionResult = "pending" | "normal" | "defects_found" | "inconclusive";
export type ReviewStatus = "tentative" | "confirmed" | "rejected" | "needs_review";
export type DetectionLabel = "AJ" | "BX" | "FS" | "PL" | "BLOCK" | "OTHER";
export type DetectorLabel = Exclude<DetectionLabel, "OTHER">;

export type EvidenceRef = {
  asset: AssetRef;
  region: { width: number; height: number; bbox_xyxy: [number, number, number, number] } | null;
  frame_index: number | null;
  timestamp_seconds: number | null;
};

export type Finding = {
  finding_id: string;
  task_id: string;
  canonical_label: DetectionLabel | null;
  open_label: string | null;
  review_status: ReviewStatus;
  source: "detector" | "open_detector" | "vlm" | "human";
  score: number | null;
  evidence: EvidenceRef[];
  confirmed_by: "rules" | "human" | null;
};

export type CoverageSummary = {
  sufficient_coverage: boolean;
  sufficient_quality: boolean;
  negative_audit_complete: boolean;
};

export type TaskSnapshot = {
  schema_version: "1";
  task_id: string;
  runtime_status: RuntimeStatus;
  inspection_result: InspectionResult;
  stage: string;
  stage_progress: number;
  overall_progress: number;
  processed_frames: number;
  processed_chunks: number;
  can_cancel?: boolean;
  active_tool: string | null;
  retry_count: number;
  waiting_reason: string | null;
  updated_at: string;
  findings: Finding[];
  coverage: CoverageSummary;
};

export type TaskRecord = {
  parent_task_id?: string | null;
  request_id: string;
  session_id: string;
  input_asset: AssetRef;
  instruction: string;
  created_at: string;
  snapshot: TaskSnapshot;
};

export type SessionRecord = {
  session_id: string;
  created_at: string;
};

export type SessionDetail = {
  session: SessionRecord;
  tasks: TaskRecord[];
};

export type TaskSubmission = {
  request_id: string;
  session_id: string;
  input_asset: AssetRef;
  instruction: string;
};

export type TurnStatus = "queued" | "running" | "completed" | "failed";
export type TurnStage = "queued" | "planning" | "executing" | "finished";

export type TurnSubmission = {
  request_id: string;
  text: string;
  input_asset: AssetRef | null;
  task_id: string | null;
  reference_task_id?: string | null;
};

export type TurnRecord = TurnSubmission & {
  session_id: string;
  created_at: string;
  updated_at: string;
  status: TurnStatus;
  stage: TurnStage;
  reply: string | null;
  linked_task_id: string | null;
  error_code: string | null;
  attempts: number;
};

export type ComparisonLabel = DetectionLabel | "UNLABELED";

export type StatusCounts = {
  confirmed: number;
  tentative: number;
  needs_review: number;
  rejected: number;
};

export type ComparisonRow = {
  label: ComparisonLabel;
  reference: StatusCounts;
  current: StatusCounts;
  confirmed_delta: number;
};

export type ComparisonResult = {
  schema_version: "1";
  tool_name: "compare";
  tool_version: "saved-comparison-v1";
  request_id: string;
  created_at: string;
  attempt: number;
  reference: TaskRecord;
  current: TaskRecord;
  reference_media: AssetRecord;
  current_media: AssetRecord;
  rows: ComparisonRow[];
  matching_status: "unverified";
  scope: "saved_findings";
};

export type FindingReviewAction = "confirm" | "reject" | "relabel";

export type FindingReview = {
  finding_id: string;
  action: FindingReviewAction;
  label: DetectionLabel | null;
  open_label: string | null;
  reason: string;
};

export type HumanReviewSubmission = {
  request_id: string;
  reviewer: string;
  snapshot_updated_at: string;
  decisions: FindingReview[];
  negative_audit_complete: boolean;
  note: string;
};

export type HumanReviewRecord = {
  task_id: string;
  submission: HumanReviewSubmission;
  before: TaskSnapshot;
  after: TaskSnapshot;
  reviewed_at: string;
};

export type HumanReviewDetail = {
  task_id: string;
  can_review: boolean;
  snapshot: TaskSnapshot;
  record: HumanReviewRecord | null;
};

export type QualitySummary = {
  mean_luminance: number;
  contrast: number;
  sharpness: number;
  adequate: boolean;
  reasons: string[];
};

export type ImageRegion = {
  width: number;
  height: number;
  bbox_xyxy: [number, number, number, number];
};

export type EnhancementKind = "low_light" | "dehaze" | "deblur" | "super_resolution";

export type EnhancementOptions = {
  kind: EnhancementKind;
  roi_xyxy: [number, number, number, number] | null;
};

export type EnhancementProposal = {
  options: EnhancementOptions | null;
  degradation:
    | "low_light"
    | "mixed_dark_noise_blur"
    | "motion_blur"
    | "haze"
    | "small_detail"
    | "none"
    | "unsupported";
  confidence: "clear" | "uncertain";
  evidence: string;
  inspection_impact: string;
};

export type EnhancementSelection = {
  status: "selected" | "skipped";
  reason: string;
  proposal: EnhancementProposal | null;
  options: EnhancementOptions | null;
};

export type EnhancementResult = {
  schema_version: "1";
  input_asset: AssetRef;
  output_asset: AssetRecord & { kind: "enhanced_image" };
  kind: EnhancementKind;
  model_id: string;
  model_version: string;
  source_region: ImageRegion;
  quality_before: QualitySummary;
  quality_after: QualitySummary;
  review_required: true;
};

export type EnhancementReview = {
  quality_effect: "improved" | "unchanged" | "degraded" | "uncertain";
  conflicting_finding_ids: string[];
  additional_anomalies: Array<{
    label: DetectionLabel;
    name: string | null;
    bbox_xyxy: [number, number, number, number] | null;
    reason: string;
  }>;
  summary: string;
};

export type EnhancementDetail = {
  task_id: string;
  result: EnhancementResult | null;
  review: EnhancementReview | null;
  selection?: EnhancementSelection | null;
};

export type SegmentationResult = {
  schema_version: "1";
  input_asset: AssetRef;
  prompt: ImageRegion;
  mask_asset: AssetRecord & { kind: "mask"; width: number; height: number; parent: AssetRef };
  overlay_asset: AssetRecord & { kind: "mask"; width: number; height: number; parent: AssetRef };
  model_id: string;
  model_version: string;
  model_score: number;
  area_pixels: number;
  mask_region: ImageRegion;
  review_required: true;
};

export type RelativeDepthResult = {
  schema_version: "1";
  input_asset: AssetRef;
  raw_asset: AssetRecord & { kind: "depth_map"; width: number; height: number; parent: AssetRef };
  preview_asset: AssetRecord & { kind: "depth_map"; width: number; height: number; parent: AssetRef };
  model_id: string;
  model_version: string;
  larger_is: "nearer" | "farther";
  raw_min: number;
  raw_max: number;
  units: "relative_model_units";
  preview_mapping: "per_image_minmax_white_near";
  review_required: true;
};

export type RelativeDepthSummary = {
  q10: number;
  median: number;
  q90: number;
  larger_is: "nearer" | "farther";
  units: "relative_model_units";
};

export type GeometryMeasurements = {
  input_asset: AssetRef;
  area_pixels: number;
  image_plane_fraction: number;
  fraction_denominator: "full_image_pixels";
  bbox_width_pixels: number;
  bbox_height_pixels: number;
  relative_depth: RelativeDepthSummary | null;
  metric_status: "unavailable_no_calibration";
  review_required: true;
};

export type GeometryDetail = {
  task_id: string;
  segmentation: SegmentationResult | null;
  depth: RelativeDepthResult | null;
  measurements: GeometryMeasurements | null;
};

export type FrameLocator = {
  frame_index: number;
  timestamp_seconds: number;
  width: number;
  height: number;
  quality: QualitySummary;
};

export type EventEvidence = FrameLocator & {
  detection_index: number | null;
  region: NonNullable<EvidenceRef["region"]>;
  score: number;
};

export type VideoEvent = {
  event_id: string;
  label: DetectorLabel;
  start_frame: number;
  end_frame: number;
  start_seconds: number;
  end_seconds: number;
  observations: number;
  last_region: NonNullable<EvidenceRef["region"]>;
  evidence: EventEvidence[];
};

export type VideoAggregationConfig = {
  policy_version: string;
  iou_threshold: number;
  max_gap_frames: number;
  max_gap_seconds: number;
  evidence_separation_seconds: number;
  audit_interval_seconds: number;
};

export type VideoAggregationState = {
  source: AssetRef;
  config: VideoAggregationConfig;
  detector_id: string | null;
  model_version: string | null;
  label_space_version: string | null;
  next_frame: number;
  last_timestamp_seconds: number | null;
  adequate_frames: number;
  active: VideoEvent[];
  events: VideoEvent[];
  audit_frames: FrameLocator[];
  finalized: boolean;
};

export type VideoScanResult = {
  state: VideoAggregationState;
  decode_complete: boolean;
  error_code: string | null;
  inspection_result: "pending";
};

export type VideoEvidenceFrame = {
  frame_index: number;
  timestamp_seconds: number;
  asset: AssetRef;
};

export type VideoEvidence = {
  frames: VideoEvidenceFrame[];
  review_frames: number[];
};

export type VideoDetail = {
  task_id: string;
  scan: VideoScanResult | null;
  evidence: VideoEvidence | null;
};
