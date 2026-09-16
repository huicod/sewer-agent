import type {
  AssetRecord,
  AssetRef,
  ComparisonResult,
  EnhancementDetail,
  Finding,
  FindingReview,
  GeometryDetail,
  HumanReviewDetail,
  HumanReviewRecord,
  HumanReviewSubmission,
  ImageEvidenceDetail,
  ImageRegion,
  RuntimeStatus,
  SessionDetail,
  SessionRecord,
  TaskRecord,
  TaskSnapshot,
  TurnRecord,
  TurnStage,
  TurnStatus,
  VideoDetail,
  VideoEvent,
} from "./types";

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const RUNTIME_STATUSES = new Set<RuntimeStatus>([
  "queued",
  "running",
  "waiting_review",
  "completed",
  "partial",
  "failed",
  "cancelled",
]);
const INSPECTION_RESULTS = new Set([
  "pending",
  "normal",
  "defects_found",
  "inconclusive",
]);
const TURN_STATUSES = new Set<TurnStatus>(["queued", "running", "completed", "failed"]);
const TURN_STAGES = new Set<TurnStage>(["queued", "planning", "executing", "finished"]);
const TURN_FIELDS = new Set([
  "request_id", "session_id", "text", "input_asset", "task_id", "created_at", "updated_at",
  "status", "stage", "reply", "linked_task_id", "error_code", "attempts",
]);
const TURN_FIELDS_WITH_REFERENCE = new Set([...TURN_FIELDS, "reference_task_id"]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function hasExactFields(value: Record<string, unknown>, fields: Set<string>): boolean {
  return Object.keys(value).length === fields.size && Object.keys(value).every((key) => fields.has(key));
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

function isUuid(value: unknown): value is string {
  return typeof value === "string" && UUID_PATTERN.test(value);
}

function isTimestamp(value: unknown): value is string {
  return typeof value === "string" && Number.isFinite(Date.parse(value));
}

function isAwareTimestamp(value: unknown): value is string {
  return isTimestamp(value) && /(?:z|[+-]\d{2}:\d{2})$/i.test(value);
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

function isNullablePositiveNumber(value: unknown): value is number | null {
  return value === null || (typeof value === "number" && Number.isFinite(value) && value > 0);
}

function isProgress(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1;
}

function isNonNegativeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

export function isAssetRef(value: unknown): value is AssetRef {
  return (
    isRecord(value) &&
    isUuid(value.asset_id) &&
    typeof value.version === "number" &&
    Number.isInteger(value.version) &&
    value.version >= 1
  );
}

export function isAssetRecord(value: unknown): value is AssetRecord {
  return (
    isRecord(value) &&
    isAssetRef(value.ref) &&
    isNonEmptyString(value.kind) &&
    isNonEmptyString(value.media_type) &&
    typeof value.byte_size === "number" &&
    Number.isInteger(value.byte_size) &&
    value.byte_size > 0 &&
    isNullablePositiveNumber(value.width) &&
    isNullablePositiveNumber(value.height) &&
    isNullablePositiveNumber(value.duration_seconds) &&
    (value.parent === null || isAssetRef(value.parent))
  );
}

export function isSessionRecord(value: unknown): value is SessionRecord {
  return isRecord(value) && isUuid(value.session_id) && isTimestamp(value.created_at);
}

function isNullableNonNegativeNumber(value: unknown): value is number | null {
  return value === null || (typeof value === "number" && Number.isFinite(value) && value >= 0);
}

function isEvidenceRef(value: unknown): boolean {
  if (!isRecord(value) || !isAssetRef(value.asset)) return false;
  if (!isNullableNonNegativeNumber(value.timestamp_seconds)) return false;
  if (!(value.frame_index === null || isNonNegativeInteger(value.frame_index))) return false;
  if ((value.frame_index === null) !== (value.timestamp_seconds === null)) return false;
  if (value.region === null) return true;
  if (
    !isRecord(value.region) ||
    !Number.isInteger(value.region.width) ||
    !Number.isInteger(value.region.height)
  ) {
    return false;
  }
  const width = value.region.width as number;
  const height = value.region.height as number;
  if (
    width <= 0 ||
    height <= 0 ||
    !Array.isArray(value.region.bbox_xyxy) ||
    value.region.bbox_xyxy.length !== 4
  ) {
    return false;
  }
  const [x1, y1, x2, y2] = value.region.bbox_xyxy;
  return (
    [x1, y1, x2, y2].every(
      (item) => typeof item === "number" && Number.isFinite(item),
    ) &&
    0 <= x1 && x1 < x2 && x2 <= width &&
    0 <= y1 && y1 < y2 && y2 <= height
  );
}

function isFinding(value: unknown): value is Finding {
  const labels = new Set(["AJ", "BX", "FS", "PL", "BLOCK", "OTHER"]);
  const statuses = new Set(["tentative", "confirmed", "rejected", "needs_review"]);
  const sources = new Set(["detector", "open_detector", "vlm", "human"]);
  return (
    isRecord(value) &&
    isUuid(value.finding_id) &&
    isUuid(value.task_id) &&
    (value.canonical_label === null ||
      (typeof value.canonical_label === "string" && labels.has(value.canonical_label))) &&
    (value.open_label === null || isNonEmptyString(value.open_label)) &&
    typeof value.review_status === "string" &&
    statuses.has(value.review_status) &&
    typeof value.source === "string" &&
    sources.has(value.source) &&
    (value.score === null || isProgress(value.score)) &&
    Array.isArray(value.evidence) &&
    value.evidence.length > 0 &&
    value.evidence.every(isEvidenceRef) &&
    (value.confirmed_by === null ||
      value.confirmed_by === "rules" ||
      value.confirmed_by === "human")
  );
}

function isCoverage(value: unknown): boolean {
  return (
    isRecord(value) &&
    typeof value.sufficient_coverage === "boolean" &&
    typeof value.sufficient_quality === "boolean" &&
    typeof value.negative_audit_complete === "boolean"
  );
}

export function isTaskSnapshot(value: unknown): value is TaskSnapshot {
  return (
    isRecord(value) &&
    value.schema_version === "1" &&
    isUuid(value.task_id) &&
    typeof value.runtime_status === "string" &&
    RUNTIME_STATUSES.has(value.runtime_status as RuntimeStatus) &&
    typeof value.inspection_result === "string" &&
    INSPECTION_RESULTS.has(value.inspection_result) &&
    isNonEmptyString(value.stage) &&
    isProgress(value.stage_progress) &&
    isProgress(value.overall_progress) &&
    isNonNegativeInteger(value.processed_frames) &&
    isNonNegativeInteger(value.processed_chunks) &&
    (value.can_cancel === undefined || typeof value.can_cancel === "boolean") &&
    isNullableString(value.active_tool) &&
    isNonNegativeInteger(value.retry_count) &&
    isNullableString(value.waiting_reason) &&
    isTimestamp(value.updated_at) &&
    Array.isArray(value.findings) && value.findings.every(isFinding) &&
    isCoverage(value.coverage)
  );
}

export function isTaskRecord(value: unknown): value is TaskRecord {
  const parentTaskId = isRecord(value) ? value.parent_task_id : undefined;
  return (
    isRecord(value) &&
    isUuid(value.request_id) &&
    isUuid(value.session_id) &&
    isAssetRef(value.input_asset) &&
    isNonEmptyString(value.instruction) &&
    isTimestamp(value.created_at) &&
    isTaskSnapshot(value.snapshot) &&
    (value.parent_task_id === undefined || value.parent_task_id === null ||
      (isUuid(value.parent_task_id) && value.parent_task_id !== value.snapshot.task_id)) &&
    (value.frame_source === undefined || value.frame_source === null ||
      (isUuid(parentTaskId) && isVideoFrameSource(value.frame_source)))
  );
}

const VIDEO_FRAME_SOURCE_FIELDS = new Set([
  "video_asset", "frame_index", "timestamp_seconds",
]);

function isVideoFrameSource(value: unknown): boolean {
  return isRecord(value) && hasExactFields(value, VIDEO_FRAME_SOURCE_FIELDS) &&
    isRecord(value.video_asset) &&
    hasExactFields(value.video_asset, new Set(["asset_id", "version"])) &&
    isAssetRef(value.video_asset) && isNonNegativeInteger(value.frame_index) &&
    isNonNegativeFinite(value.timestamp_seconds);
}

export function isSessionDetail(value: unknown): value is SessionDetail {
  return (
    isRecord(value) &&
    isSessionRecord(value.session) &&
    Array.isArray(value.tasks) &&
    value.tasks.every(isTaskRecord)
  );
}

export function isTurnRecord(value: unknown): value is TurnRecord {
  return (
    isRecord(value) &&
    (hasExactFields(value, TURN_FIELDS) || hasExactFields(value, TURN_FIELDS_WITH_REFERENCE)) &&
    isUuid(value.request_id) &&
    isUuid(value.session_id) &&
    typeof value.text === "string" &&
    value.text.length >= 1 && value.text.length <= 4_000 &&
    (value.input_asset === null || isAssetRef(value.input_asset)) &&
    (value.task_id === null || isUuid(value.task_id)) &&
    (value.reference_task_id === undefined || value.reference_task_id === null ||
      isUuid(value.reference_task_id)) &&
    isTimestamp(value.created_at) &&
    isTimestamp(value.updated_at) &&
    typeof value.status === "string" && TURN_STATUSES.has(value.status as TurnStatus) &&
    typeof value.stage === "string" && TURN_STAGES.has(value.stage as TurnStage) &&
    isNullableString(value.reply) &&
    (value.linked_task_id === null || isUuid(value.linked_task_id)) &&
    isNullableString(value.error_code) &&
    isNonNegativeInteger(value.attempts)
  );
}

export function isTurnList(value: unknown): value is TurnRecord[] {
  return Array.isArray(value) && value.every(isTurnRecord);
}

const DETECTION_LABELS = new Set(["AJ", "BX", "FS", "PL", "BLOCK", "OTHER"]);
const VIDEO_LABELS = new Set(["AJ", "BX", "FS", "PL", "BLOCK"]);
const REVIEW_ACTIONS = new Set(["confirm", "reject", "relabel"]);

function isFindingReview(value: unknown): value is FindingReview {
  if (
    !isRecord(value) ||
    !isUuid(value.finding_id) ||
    typeof value.action !== "string" ||
    !REVIEW_ACTIONS.has(value.action) ||
    !(value.label === null || (typeof value.label === "string" && DETECTION_LABELS.has(value.label))) ||
    !(value.open_label === null || isNonEmptyString(value.open_label)) ||
    typeof value.reason !== "string" ||
    value.reason.trim().length < 1 ||
    value.reason.length > 1_000
  ) return false;
  if (value.action === "relabel") {
    return value.label !== null &&
      (value.label === "OTHER" ? value.open_label !== null : value.open_label === null);
  }
  if (value.action === "reject") return value.label === null && value.open_label === null;
  return value.label === null;
}

function isHumanReviewSubmission(value: unknown): value is HumanReviewSubmission {
  if (
    !isRecord(value) || !isUuid(value.request_id) ||
    typeof value.reviewer !== "string" || value.reviewer.trim().length < 1 || value.reviewer.length > 100 ||
    !isAwareTimestamp(value.snapshot_updated_at) || !Array.isArray(value.decisions) ||
    value.decisions.length > 2_000 || !value.decisions.every(isFindingReview) ||
    typeof value.negative_audit_complete !== "boolean" ||
    typeof value.note !== "string" || value.note.length > 2_000
  ) return false;
  const ids = value.decisions.map((decision) => decision.finding_id);
  return ids.length === new Set(ids).size &&
    new TextEncoder().encode(JSON.stringify(value)).byteLength <= 1_000_000;
}

export function isHumanReviewRecord(value: unknown): value is HumanReviewRecord {
  return (
    isRecord(value) && isUuid(value.task_id) &&
    isHumanReviewSubmission(value.submission) &&
    isTaskSnapshot(value.before) && isTaskSnapshot(value.after) &&
    value.before.task_id === value.task_id && value.after.task_id === value.task_id &&
    isAwareTimestamp(value.reviewed_at)
  );
}

export function isHumanReviewDetail(value: unknown): value is HumanReviewDetail {
  return (
    isRecord(value) && isUuid(value.task_id) && typeof value.can_review === "boolean" &&
    isTaskSnapshot(value.snapshot) && value.snapshot.task_id === value.task_id &&
    (value.record === null || (isHumanReviewRecord(value.record) && value.record.task_id === value.task_id))
  );
}

function isPositiveFinite(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value > 0;
}

function isNonNegativeFinite(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0;
}

function isRegion(value: unknown): boolean {
  if (!isRecord(value) || !Number.isInteger(value.width) || !Number.isInteger(value.height)) return false;
  const width = value.width as number;
  const height = value.height as number;
  if (width <= 0 || height <= 0 || !Array.isArray(value.bbox_xyxy) || value.bbox_xyxy.length !== 4) return false;
  const [x1, y1, x2, y2] = value.bbox_xyxy;
  return [x1, y1, x2, y2].every(isNonNegativeFinite) && x1 < x2 && x2 <= width && y1 < y2 && y2 <= height;
}

function isQuality(value: unknown): boolean {
  return isRecord(value) && isNonNegativeFinite(value.mean_luminance) &&
    isNonNegativeFinite(value.contrast) && isNonNegativeFinite(value.sharpness) &&
    typeof value.adequate === "boolean" && Array.isArray(value.reasons) &&
    value.reasons.every(isNonEmptyString);
}

function isFrameLocator(value: unknown): boolean {
  return isRecord(value) && isNonNegativeInteger(value.frame_index) &&
    isNonNegativeFinite(value.timestamp_seconds) && Number.isInteger(value.width) &&
    (value.width as number) > 0 && Number.isInteger(value.height) && (value.height as number) > 0 &&
    isQuality(value.quality);
}

function isEventEvidence(value: unknown): boolean {
  return isFrameLocator(value) && isRecord(value) &&
    (value.detection_index === null || isNonNegativeInteger(value.detection_index)) &&
    isRegion(value.region) && isProgress(value.score) &&
    (value.region as { width: number }).width === value.width &&
    (value.region as { height: number }).height === value.height;
}

function isVideoEvent(value: unknown): value is VideoEvent {
  if (
    !isRecord(value) || !isNonEmptyString(value.event_id) ||
    typeof value.label !== "string" || !VIDEO_LABELS.has(value.label) ||
    !isNonNegativeInteger(value.start_frame) || !isNonNegativeInteger(value.end_frame) ||
    !isNonNegativeFinite(value.start_seconds) || !isNonNegativeFinite(value.end_seconds) ||
    !Number.isInteger(value.observations) || (value.observations as number) < 1 ||
    !isRegion(value.last_region) || !Array.isArray(value.evidence) ||
    value.evidence.length < 1 || value.evidence.length > 3 || !value.evidence.every(isEventEvidence)
  ) return false;
  const startFrame = value.start_frame as number;
  const endFrame = value.end_frame as number;
  if (endFrame < startFrame || value.end_seconds < value.start_seconds) return false;
  if ((value.observations as number) > endFrame - startFrame + 1) return false;
  return value.evidence.every((item) => item.frame_index >= startFrame && item.frame_index <= endFrame);
}

function isVideoConfig(value: unknown): boolean {
  return isRecord(value) && typeof value.policy_version === "string" &&
    isPositiveFinite(value.iou_threshold) && value.iou_threshold <= 1 &&
    isNonNegativeInteger(value.max_gap_frames) && isPositiveFinite(value.max_gap_seconds) &&
    isNonNegativeFinite(value.evidence_separation_seconds) && isPositiveFinite(value.audit_interval_seconds);
}

function isVideoScan(value: unknown): boolean {
  if (!isRecord(value) || !isRecord(value.state)) return false;
  const state = value.state;
  if (
    !isAssetRef(state.source) || !isVideoConfig(state.config) ||
    !isNullableString(state.detector_id) || !isNullableString(state.model_version) ||
    !isNullableString(state.label_space_version) || !isNonNegativeInteger(state.next_frame) ||
    !isNullableNonNegativeNumber(state.last_timestamp_seconds) || !isNonNegativeInteger(state.adequate_frames) ||
    (state.adequate_frames as number) > (state.next_frame as number) ||
    !Array.isArray(state.active) || !state.active.every(isVideoEvent) ||
    !Array.isArray(state.events) || !state.events.every(isVideoEvent) ||
    !Array.isArray(state.audit_frames) || !state.audit_frames.every(isFrameLocator) ||
    typeof state.finalized !== "boolean" || (state.finalized && state.active.length > 0) ||
    typeof value.decode_complete !== "boolean" ||
    !(value.error_code === null || isNonEmptyString(value.error_code)) ||
    value.inspection_result !== "pending"
  ) return false;
  if ((state.next_frame as number) > 0 && (
    state.last_timestamp_seconds === null || !state.detector_id || !state.model_version || !state.label_space_version
  )) return false;
  if (value.decode_complete && (value.error_code !== null || state.next_frame === 0)) return false;
  const ids = [...state.events, ...state.active].map((event) => event.event_id);
  return ids.length === new Set(ids).size;
}

function isVideoSelection(value: unknown, reviewFrames: unknown[]): boolean {
  if (!isRecord(value) || value.version !== "video-selection-v1" ||
    !isNonNegativeInteger(value.candidate_count) || !isNonNegativeInteger(value.deferred_count) ||
    !isNonNegativeInteger(value.unreviewed_event_count) || !isNonNegativeInteger(value.unreviewed_audit_count) ||
    !isNonNegativeInteger(value.scene_calls) || (value.scene_calls as number) > 4 ||
    !Array.isArray(value.scene_errors) || !value.scene_errors.every(isNonEmptyString) ||
    value.scene_errors.length > (value.scene_calls as number) ||
    !Array.isArray(value.pool_frames) || value.pool_frames.length > 64 ||
    !value.pool_frames.every(isNonNegativeInteger) || new Set(value.pool_frames).size !== value.pool_frames.length ||
    (value.candidate_count as number) < value.pool_frames.length ||
    !Array.isArray(value.review_frames) || new Set(value.review_frames).size !== value.review_frames.length ||
    value.review_frames.length !== reviewFrames.length ||
    !value.review_frames.every((frame, index) => value.pool_frames instanceof Array &&
      value.pool_frames.includes(frame) && frame === reviewFrames[index]) ||
    value.deferred_count !== (value.candidate_count as number) - value.review_frames.length ||
    !Array.isArray(value.assessments) || value.assessments.length > 64
  ) return false;
  const pool = new Set(value.pool_frames);
  const assessmentFrames = new Set<unknown>();
  return value.assessments.every((assessment) => {
    if (!isRecord(assessment) || !isNonNegativeInteger(assessment.frame_index) || !pool.has(assessment.frame_index) ||
      assessmentFrames.has(assessment.frame_index) ||
      !["pipe", "outside", "unassessable", "unknown"].includes(assessment.scene as string) ||
      !isNonEmptyString(assessment.reason) || assessment.reason.length > 300) return false;
    assessmentFrames.add(assessment.frame_index);
    return true;
  });
}

function isVideoEvidence(value: unknown): boolean {
  if (!isRecord(value) || !Array.isArray(value.frames) || !Array.isArray(value.review_frames)) return false;
  return value.frames.every((frame) =>
    isRecord(frame) && isNonNegativeInteger(frame.frame_index) &&
    isNonNegativeFinite(frame.timestamp_seconds) && isAssetRef(frame.asset),
  ) && value.review_frames.every(isNonNegativeInteger) &&
    (value.selection === undefined || isVideoSelection(value.selection, value.review_frames));
}

export function isVideoDetail(value: unknown): value is VideoDetail {
  return isRecord(value) && isUuid(value.task_id) &&
    (value.scan === null || isVideoScan(value.scan)) &&
    (value.evidence === null || isVideoEvidence(value.evidence));
}

const ASSET_RECORD_FIELDS = new Set([
  "ref", "kind", "media_type", "byte_size", "width", "height", "duration_seconds", "parent",
]);
const ASSET_REF_FIELDS = new Set(["asset_id", "version"]);
const REGION_FIELDS = new Set(["width", "height", "bbox_xyxy"]);
const QUALITY_FIELDS = new Set(["mean_luminance", "contrast", "sharpness", "adequate", "reasons"]);
const IMAGE_EVIDENCE_FIELDS = new Set([
  "task_id", "input_asset", "parent_task_id", "observation", "review", "fusion_changes", "findings",
]);
const OBSERVATION_FIELDS = new Set(["detection", "quality"]);
const DETECTION_BATCH_FIELDS = new Set([
  "detector_id", "model_version", "label_space_version", "width", "height", "detections",
]);
const ORIGINAL_DETECTION_FIELDS = new Set(["label", "native_label", "score", "bbox_xyxy"]);
const EVIDENCE_REVIEW_FIELDS = new Set([
  "image_assessable", "full_image_audited", "candidates", "additional_anomalies", "summary",
]);
const EVIDENCE_CANDIDATE_FIELDS = new Set([
  "index", "verdict", "proposed_label", "reason", "evidence_strength",
]);
const EVIDENCE_ANOMALY_FIELDS = new Set([
  "label", "name", "bbox_xyxy", "reason", "evidence_strength",
]);
const FUSION_CHANGE_FIELDS = new Set([
  "index", "additional_index", "action", "pending", "label", "bbox_xyxy", "reason",
  "vlm_verdict", "proposed_label", "gate",
]);
const IMAGE_EVIDENCE_FINDING_FIELDS = new Set([
  "finding_id", "task_id", "canonical_label", "open_label", "review_status", "source", "score",
  "evidence", "confirmed_by",
]);
const ENHANCEMENT_RESULT_FIELDS = new Set([
  "schema_version", "input_asset", "output_asset", "kind", "model_id", "model_version",
  "source_region", "quality_before", "quality_after", "review_required",
]);
const ENHANCEMENT_REVIEW_FIELDS = new Set([
  "quality_effect", "conflicting_finding_ids", "additional_anomalies", "summary",
]);
const ENHANCEMENT_SELECTION_FIELDS = new Set(["status", "reason", "proposal", "options"]);
const ENHANCEMENT_PROPOSAL_FIELDS = new Set([
  "options", "degradation", "confidence", "evidence", "inspection_impact",
]);
const ENHANCEMENT_OPTIONS_FIELDS = new Set(["kind", "roi_xyxy"]);
const ADDITIONAL_ANOMALY_FIELDS = new Set(["label", "name", "bbox_xyxy", "reason"]);
const ENHANCEMENT_KINDS = new Set(["low_light", "dehaze", "deblur", "super_resolution"]);
const DEGRADATIONS = new Set([
  "low_light", "mixed_dark_noise_blur", "motion_blur", "haze", "small_detail", "none", "unsupported",
]);
const QUALITY_EFFECTS = new Set(["improved", "unchanged", "degraded", "uncertain"]);

function sameAsset(left: AssetRef, right: AssetRef): boolean {
  return left.asset_id === right.asset_id && left.version === right.version;
}

function isExactAssetRef(value: unknown): value is AssetRef {
  return isRecord(value) && hasExactFields(value, ASSET_REF_FIELDS) && isAssetRef(value);
}

function isExactAssetRecord(value: unknown): value is AssetRecord {
  return isRecord(value) && hasExactFields(value, ASSET_RECORD_FIELDS) && isAssetRecord(value) &&
    isExactAssetRef(value.ref) && (value.parent === null || isExactAssetRef(value.parent));
}

function isFiniteBox(value: unknown): value is [number, number, number, number] {
  return Array.isArray(value) && value.length === 4 && value.every(
    (coordinate) => typeof coordinate === "number" && Number.isFinite(coordinate),
  );
}

function isBoundedBox(value: unknown, width: number, height: number): boolean {
  if (!isFiniteBox(value)) return false;
  const [x1, y1, x2, y2] = value;
  return 0 <= x1 && x1 < x2 && x2 <= width && 0 <= y1 && y1 < y2 && y2 <= height;
}

function isEvidenceStrength(value: unknown): boolean {
  return value === null || value === "clear" || value === "uncertain";
}

function isImageObservation(value: unknown): boolean {
  if (
    !isRecord(value) || !hasExactFields(value, OBSERVATION_FIELDS) ||
    !isRecord(value.detection) || !hasExactFields(value.detection, DETECTION_BATCH_FIELDS) ||
    !isNonEmptyString(value.detection.detector_id) ||
    !isNonEmptyString(value.detection.model_version) ||
    !isNonEmptyString(value.detection.label_space_version) ||
    !Number.isInteger(value.detection.width) || (value.detection.width as number) <= 0 ||
    !Number.isInteger(value.detection.height) || (value.detection.height as number) <= 0 ||
    !Array.isArray(value.detection.detections) ||
    !isRecord(value.quality) || !hasExactFields(value.quality, QUALITY_FIELDS) || !isQuality(value.quality)
  ) return false;
  const width = value.detection.width as number;
  const height = value.detection.height as number;
  return value.detection.detections.every((detection) =>
    isRecord(detection) && hasExactFields(detection, ORIGINAL_DETECTION_FIELDS) &&
    typeof detection.label === "string" && VIDEO_LABELS.has(detection.label) &&
    isNonEmptyString(detection.native_label) && isProgress(detection.score) &&
    isBoundedBox(detection.bbox_xyxy, width, height),
  );
}

function isEvidenceReview(value: unknown): boolean {
  if (
    !isRecord(value) || !hasExactFields(value, EVIDENCE_REVIEW_FIELDS) ||
    typeof value.image_assessable !== "boolean" || typeof value.full_image_audited !== "boolean" ||
    !Array.isArray(value.candidates) || !Array.isArray(value.additional_anomalies) ||
    !isNonEmptyString(value.summary) || value.summary.length > 2_000
  ) return false;
  const verdicts = new Set(["accept", "revise", "reject", "abstain", "need_more_context"]);
  return value.candidates.every((candidate) =>
    isRecord(candidate) && hasExactFields(candidate, EVIDENCE_CANDIDATE_FIELDS) &&
    isNonNegativeInteger(candidate.index) && typeof candidate.verdict === "string" &&
    verdicts.has(candidate.verdict) &&
    (candidate.proposed_label === null ||
      (typeof candidate.proposed_label === "string" && VIDEO_LABELS.has(candidate.proposed_label))) &&
    isNonEmptyString(candidate.reason) && candidate.reason.length <= 1_000 &&
    isEvidenceStrength(candidate.evidence_strength),
  ) && value.additional_anomalies.every((anomaly) =>
    isRecord(anomaly) && hasExactFields(anomaly, EVIDENCE_ANOMALY_FIELDS) &&
    typeof anomaly.label === "string" && DETECTION_LABELS.has(anomaly.label) &&
    (anomaly.name === null || typeof anomaly.name === "string") &&
    (anomaly.bbox_xyxy === null || isFiniteBox(anomaly.bbox_xyxy)) &&
    isNonEmptyString(anomaly.reason) && anomaly.reason.length <= 1_000 &&
    isEvidenceStrength(anomaly.evidence_strength),
  );
}

function isFusionChange(value: unknown): boolean {
  return isRecord(value) && hasExactFields(value, FUSION_CHANGE_FIELDS) &&
    (value.index === null || isNonNegativeInteger(value.index)) &&
    (value.additional_index === null || isNonNegativeInteger(value.additional_index)) &&
    typeof value.action === "string" && typeof value.pending === "boolean" &&
    typeof value.label === "string" && DETECTION_LABELS.has(value.label) &&
    (value.bbox_xyxy === null || isFiniteBox(value.bbox_xyxy)) &&
    typeof value.reason === "string" &&
    (value.vlm_verdict === null || typeof value.vlm_verdict === "string") &&
    (value.proposed_label === null ||
      (typeof value.proposed_label === "string" && VIDEO_LABELS.has(value.proposed_label))) &&
    (value.gate === null || typeof value.gate === "string");
}

export function isImageEvidenceDetail(value: unknown): value is ImageEvidenceDetail {
  if (
    !isRecord(value) || !hasExactFields(value, IMAGE_EVIDENCE_FIELDS) || !isUuid(value.task_id) ||
    !isExactAssetRef(value.input_asset) ||
    !(value.parent_task_id === null ||
      (isUuid(value.parent_task_id) && value.parent_task_id !== value.task_id)) ||
    !(value.observation === null || isImageObservation(value.observation)) ||
    !(value.review === null || isEvidenceReview(value.review)) ||
    !Array.isArray(value.fusion_changes) || !value.fusion_changes.every(isFusionChange) ||
    !Array.isArray(value.findings) || !value.findings.every((finding) =>
      isRecord(finding) && hasExactFields(finding, IMAGE_EVIDENCE_FINDING_FIELDS) &&
      isFinding(finding) && finding.task_id === value.task_id)
  ) return false;
  return true;
}

function isEnhancementQuality(value: unknown): boolean {
  return isRecord(value) && hasExactFields(value, QUALITY_FIELDS) && isQuality(value);
}

function isEnhancementRegion(value: unknown): value is ImageRegion {
  return isRecord(value) && hasExactFields(value, REGION_FIELDS) && isRegion(value);
}

function isAnomalyBox(value: unknown, width: number, height: number): boolean {
  if (!Array.isArray(value) || value.length !== 4 || !value.every(isNonNegativeFinite)) return false;
  const [x1, y1, x2, y2] = value;
  return x1 < x2 && x2 <= width && y1 < y2 && y2 <= height;
}

function isEnhancementReview(
  value: unknown,
  sourceRegion: { width: number; height: number },
): boolean {
  if (
    !isRecord(value) || !hasExactFields(value, ENHANCEMENT_REVIEW_FIELDS) ||
    typeof value.quality_effect !== "string" || !QUALITY_EFFECTS.has(value.quality_effect) ||
    !Array.isArray(value.conflicting_finding_ids) || value.conflicting_finding_ids.length > 128 ||
    !value.conflicting_finding_ids.every(isUuid) ||
    new Set(value.conflicting_finding_ids).size !== value.conflicting_finding_ids.length ||
    !Array.isArray(value.additional_anomalies) || value.additional_anomalies.length > 12 ||
    !isNonEmptyString(value.summary) || value.summary.length > 2_000
  ) return false;
  return value.additional_anomalies.every((anomaly) =>
    isRecord(anomaly) && hasExactFields(anomaly, ADDITIONAL_ANOMALY_FIELDS) &&
    typeof anomaly.label === "string" && DETECTION_LABELS.has(anomaly.label) &&
    (anomaly.name === null || (typeof anomaly.name === "string" && anomaly.name.length <= 200)) &&
    (anomaly.bbox_xyxy === null || isAnomalyBox(anomaly.bbox_xyxy, sourceRegion.width, sourceRegion.height)) &&
    typeof anomaly.reason === "string" && anomaly.reason.trim().length > 0 && anomaly.reason.length <= 1_000,
  );
}

function isEnhancementOptions(value: unknown): boolean {
  if (
    !isRecord(value) || !hasExactFields(value, ENHANCEMENT_OPTIONS_FIELDS) ||
    typeof value.kind !== "string" || !ENHANCEMENT_KINDS.has(value.kind)
  ) return false;
  if (value.roi_xyxy === null) return true;
  if (!Array.isArray(value.roi_xyxy) || value.roi_xyxy.length !== 4 ||
      !value.roi_xyxy.every(isNonNegativeFinite)) return false;
  const [x1, y1, x2, y2] = value.roi_xyxy;
  return x1 < x2 && y1 < y2;
}

function sameEnhancementOptions(left: unknown, right: unknown): boolean {
  if (!isRecord(left) || !isRecord(right) || left.kind !== right.kind) return false;
  if (left.roi_xyxy === null || right.roi_xyxy === null) return left.roi_xyxy === right.roi_xyxy;
  const leftRoi = left.roi_xyxy;
  const rightRoi = right.roi_xyxy;
  return Array.isArray(leftRoi) && Array.isArray(rightRoi) &&
    leftRoi.every((coordinate, index) => coordinate === rightRoi[index]);
}

function isEnhancementProposal(value: unknown): boolean {
  return isRecord(value) && hasExactFields(value, ENHANCEMENT_PROPOSAL_FIELDS) &&
    (value.options === null || isEnhancementOptions(value.options)) &&
    typeof value.degradation === "string" && DEGRADATIONS.has(value.degradation) &&
    (value.confidence === "clear" || value.confidence === "uncertain") &&
    isNonEmptyString(value.evidence) && value.evidence.length <= 1_000 &&
    isNonEmptyString(value.inspection_impact) && value.inspection_impact.length <= 1_000;
}

function isEnhancementSelection(value: unknown): boolean {
  if (
    !isRecord(value) || !hasExactFields(value, ENHANCEMENT_SELECTION_FIELDS) ||
    (value.status !== "selected" && value.status !== "skipped") ||
    !isNonEmptyString(value.reason) ||
    !(value.proposal === null || isEnhancementProposal(value.proposal)) ||
    !(value.options === null || isEnhancementOptions(value.options))
  ) return false;
  if ((value.status === "selected") !== (value.options !== null)) return false;
  return value.options === null || (
    isRecord(value.proposal) && value.proposal.options !== null &&
    sameEnhancementOptions(value.options, value.proposal.options)
  );
}

export function isEnhancementDetail(value: unknown): value is EnhancementDetail {
  if (
    !isRecord(value) ||
    !(hasExactFields(value, new Set(["task_id", "result", "review"])) ||
      hasExactFields(value, new Set(["task_id", "result", "review", "selection"]))) ||
    !isUuid(value.task_id) ||
    !(value.result === null || isRecord(value.result)) ||
    !(value.selection === undefined || value.selection === null || isEnhancementSelection(value.selection))
  ) return false;
  if (value.result === null) return value.review === null;
  const result = value.result;
  if (
    !hasExactFields(result, ENHANCEMENT_RESULT_FIELDS) || result.schema_version !== "1" ||
    !isExactAssetRef(result.input_asset) || !isExactAssetRecord(result.output_asset) ||
    result.output_asset.kind !== "enhanced_image" || result.output_asset.parent === null ||
    !sameAsset(result.output_asset.parent, result.input_asset) ||
    sameAsset(result.output_asset.ref, result.input_asset) ||
    typeof result.kind !== "string" || !ENHANCEMENT_KINDS.has(result.kind) ||
    !isNonEmptyString(result.model_id) || !isNonEmptyString(result.model_version) ||
    !isEnhancementRegion(result.source_region) ||
    !isEnhancementQuality(result.quality_before) || !isEnhancementQuality(result.quality_after) ||
    result.review_required !== true
  ) return false;
  return value.review === null || isEnhancementReview(value.review, result.source_region);
}

const GEOMETRY_DETAIL_FIELDS = new Set(["task_id", "segmentation", "depth", "measurements"]);
const SEGMENTATION_FIELDS = new Set([
  "schema_version", "input_asset", "prompt", "mask_asset", "overlay_asset", "model_id",
  "model_version", "model_score", "area_pixels", "mask_region", "review_required",
]);
const DEPTH_FIELDS = new Set([
  "schema_version", "input_asset", "raw_asset", "preview_asset", "model_id", "model_version",
  "larger_is", "raw_min", "raw_max", "units", "preview_mapping", "review_required",
]);
const MEASUREMENT_FIELDS = new Set([
  "input_asset", "area_pixels", "image_plane_fraction", "fraction_denominator",
  "bbox_width_pixels", "bbox_height_pixels", "relative_depth", "metric_status",
  "review_required",
]);
const RELATIVE_SUMMARY_FIELDS = new Set(["q10", "median", "q90", "larger_is", "units"]);

function isGeometryAsset(
  value: unknown,
  source: AssetRef,
  kind: "mask" | "depth_map",
  mediaType: string,
): value is AssetRecord & { width: number; height: number; parent: AssetRef } {
  return isExactAssetRecord(value) && value.kind === kind && value.media_type === mediaType &&
    value.width !== null && value.height !== null && value.parent !== null &&
    sameAsset(value.parent, source) && !sameAsset(value.ref, source);
}

function isSegmentation(value: unknown): boolean {
  if (
    !isRecord(value) || !hasExactFields(value, SEGMENTATION_FIELDS) ||
    value.schema_version !== "1" || !isExactAssetRef(value.input_asset) ||
    !isEnhancementRegion(value.prompt) || !isEnhancementRegion(value.mask_region) ||
    !isGeometryAsset(value.mask_asset, value.input_asset, "mask", "image/png") ||
    !isGeometryAsset(value.overlay_asset, value.input_asset, "mask", "image/png") ||
    sameAsset(value.mask_asset.ref, value.overlay_asset.ref) ||
    !isNonEmptyString(value.model_id) || !isNonEmptyString(value.model_version) ||
    typeof value.model_score !== "number" || !Number.isFinite(value.model_score) ||
    !Number.isInteger(value.area_pixels) || (value.area_pixels as number) <= 0 ||
    value.review_required !== true
  ) return false;
  const prompt = value.prompt as ImageRegion;
  const region = value.mask_region as ImageRegion;
  const mask = value.mask_asset as AssetRecord;
  const overlay = value.overlay_asset as AssetRecord;
  return mask.width === prompt.width && mask.height === prompt.height &&
    overlay.width === prompt.width && overlay.height === prompt.height &&
    region.width === prompt.width && region.height === prompt.height &&
    (value.area_pixels as number) <= prompt.width * prompt.height;
}

function isDepth(value: unknown): boolean {
  if (
    !isRecord(value) || !hasExactFields(value, DEPTH_FIELDS) || value.schema_version !== "1" ||
    !isExactAssetRef(value.input_asset) ||
    !isGeometryAsset(value.raw_asset, value.input_asset, "depth_map", "application/x-npy") ||
    !isGeometryAsset(value.preview_asset, value.input_asset, "depth_map", "image/png") ||
    sameAsset(value.raw_asset.ref, value.preview_asset.ref) ||
    !isNonEmptyString(value.model_id) || !isNonEmptyString(value.model_version) ||
    (value.larger_is !== "nearer" && value.larger_is !== "farther") ||
    typeof value.raw_min !== "number" || !Number.isFinite(value.raw_min) ||
    typeof value.raw_max !== "number" || !Number.isFinite(value.raw_max) ||
    value.raw_max <= value.raw_min || value.units !== "relative_model_units" ||
    value.preview_mapping !== "per_image_minmax_white_near" || value.review_required !== true
  ) return false;
  const raw = value.raw_asset as AssetRecord;
  const preview = value.preview_asset as AssetRecord;
  return raw.width === preview.width && raw.height === preview.height;
}

function isRelativeSummary(value: unknown): boolean {
  if (!isRecord(value) || !hasExactFields(value, RELATIVE_SUMMARY_FIELDS)) return false;
  const numbers = [value.q10, value.median, value.q90];
  return numbers.every((item) => typeof item === "number" && Number.isFinite(item)) &&
    (value.q10 as number) <= (value.median as number) &&
    (value.median as number) <= (value.q90 as number) &&
    (value.larger_is === "nearer" || value.larger_is === "farther") &&
    value.units === "relative_model_units";
}

function isMeasurements(value: unknown): boolean {
  return isRecord(value) && hasExactFields(value, MEASUREMENT_FIELDS) &&
    isExactAssetRef(value.input_asset) && Number.isInteger(value.area_pixels) &&
    (value.area_pixels as number) > 0 && isProgress(value.image_plane_fraction) &&
    value.fraction_denominator === "full_image_pixels" &&
    isPositiveFinite(value.bbox_width_pixels) && isPositiveFinite(value.bbox_height_pixels) &&
    (value.relative_depth === null || isRelativeSummary(value.relative_depth)) &&
    value.metric_status === "unavailable_no_calibration" && value.review_required === true;
}

export function isGeometryDetail(value: unknown): value is GeometryDetail {
  if (
    !isRecord(value) || !hasExactFields(value, GEOMETRY_DETAIL_FIELDS) || !isUuid(value.task_id) ||
    !(value.segmentation === null || isSegmentation(value.segmentation)) ||
    !(value.depth === null || isDepth(value.depth)) ||
    !(value.measurements === null || isMeasurements(value.measurements))
  ) return false;
  const segmentation = value.segmentation as GeometryDetail["segmentation"];
  const depth = value.depth as GeometryDetail["depth"];
  const measurements = value.measurements as GeometryDetail["measurements"];
  const sources = [segmentation?.input_asset, depth?.input_asset, measurements?.input_asset]
    .filter((item): item is AssetRef => item !== undefined);
  if (sources.some((item) => !sameAsset(item, sources[0]))) return false;
  if (segmentation && depth && (
    segmentation.mask_asset.width !== depth.raw_asset.width ||
    segmentation.mask_asset.height !== depth.raw_asset.height
  )) return false;
  if (measurements && (!segmentation || measurements.area_pixels !== segmentation.area_pixels)) {
    return false;
  }
  return !measurements?.relative_depth || depth !== null;
}

const COMPARISON_LABELS = ["AJ", "BX", "FS", "PL", "BLOCK", "OTHER", "UNLABELED"] as const;
const REVIEW_STATUSES = ["confirmed", "tentative", "needs_review", "rejected"] as const;
const COMPARISON_FIELDS = new Set([
  "schema_version", "tool_name", "tool_version", "request_id", "created_at", "attempt",
  "reference", "current", "reference_media", "current_media", "rows", "matching_status", "scope",
]);
const COMPARISON_ROW_FIELDS = new Set(["label", "reference", "current", "confirmed_delta"]);
const STATUS_COUNT_FIELDS = new Set(REVIEW_STATUSES);
const TASK_FIELDS = new Set([
  "request_id", "session_id", "input_asset", "instruction", "created_at", "snapshot",
]);
const TASK_FIELDS_WITH_PARENT = new Set([...TASK_FIELDS, "parent_task_id"]);
const TASK_FIELDS_WITH_FRAME_SOURCE = new Set([
  ...TASK_FIELDS_WITH_PARENT, "frame_source",
]);
const SNAPSHOT_FIELDS = new Set([
  "schema_version", "task_id", "runtime_status", "inspection_result", "stage", "stage_progress",
  "overall_progress", "processed_frames", "processed_chunks", "active_tool", "retry_count",
  "waiting_reason", "updated_at", "findings", "coverage",
]);
const SNAPSHOT_FIELDS_WITH_CANCEL = new Set([...SNAPSHOT_FIELDS, "can_cancel"]);
const COVERAGE_FIELDS = new Set([
  "sufficient_coverage", "sufficient_quality", "negative_audit_complete",
]);
const FINDING_FIELDS = new Set([
  "finding_id", "task_id", "canonical_label", "open_label", "review_status", "source", "score",
  "evidence", "confirmed_by",
]);
const EVIDENCE_FIELDS = new Set(["asset", "region", "frame_index", "timestamp_seconds"]);

function isExactComparisonTask(value: unknown): value is TaskRecord {
  if (
    !isRecord(value) ||
    !(hasExactFields(value, TASK_FIELDS) || hasExactFields(value, TASK_FIELDS_WITH_PARENT) ||
      hasExactFields(value, TASK_FIELDS_WITH_FRAME_SOURCE)) ||
    !isTaskRecord(value) || !isRecord(value.snapshot)
  ) return false;
  const snapshot = value.snapshot;
  if (
    !(hasExactFields(snapshot, SNAPSHOT_FIELDS) || hasExactFields(snapshot, SNAPSHOT_FIELDS_WITH_CANCEL)) ||
    !isRecord(snapshot.coverage) || !hasExactFields(snapshot.coverage, COVERAGE_FIELDS) ||
    !Array.isArray(snapshot.findings)
  ) return false;
  return snapshot.findings.every((finding) =>
    isRecord(finding) && hasExactFields(finding, FINDING_FIELDS) &&
    Array.isArray(finding.evidence) && finding.evidence.every((evidence) =>
      isRecord(evidence) && hasExactFields(evidence, EVIDENCE_FIELDS) &&
      isExactAssetRef(evidence.asset) &&
      (evidence.region === null ||
        (isRecord(evidence.region) && hasExactFields(evidence.region, REGION_FIELDS))),
    ),
  );
}

function comparisonCounts(task: TaskRecord, label: string): Record<string, number> {
  const counts: Record<string, number> = {
    confirmed: 0, tentative: 0, needs_review: 0, rejected: 0,
  };
  for (const finding of task.snapshot.findings) {
    const findingLabel = finding.canonical_label ?? "UNLABELED";
    if (findingLabel === label) counts[finding.review_status] += 1;
  }
  return counts;
}

function isStatusCounts(value: unknown, expected: Record<string, number>): boolean {
  return isRecord(value) && hasExactFields(value, STATUS_COUNT_FIELDS) &&
    REVIEW_STATUSES.every((status) => isNonNegativeInteger(value[status]) && value[status] === expected[status]);
}

export function isComparisonResult(value: unknown): value is ComparisonResult {
  if (
    !isRecord(value) || !hasExactFields(value, COMPARISON_FIELDS) ||
    value.schema_version !== "1" || value.tool_name !== "compare" ||
    value.tool_version !== "saved-comparison-v1" || !isUuid(value.request_id) ||
    !isAwareTimestamp(value.created_at) || !Number.isInteger(value.attempt) ||
    (value.attempt as number) < 1 || !isExactComparisonTask(value.reference) ||
    !isExactComparisonTask(value.current) || !isExactAssetRecord(value.reference_media) ||
    !isExactAssetRecord(value.current_media) || !Array.isArray(value.rows) ||
    value.rows.length !== COMPARISON_LABELS.length || value.matching_status !== "unverified" ||
    value.scope !== "saved_findings"
  ) return false;
  const reference = value.reference;
  const current = value.current;
  const referenceMedia = value.reference_media;
  const currentMedia = value.current_media;
  if (
    reference.session_id !== current.session_id ||
    reference.snapshot.task_id === current.snapshot.task_id ||
    !sameAsset(reference.input_asset, referenceMedia.ref) ||
    !sameAsset(current.input_asset, currentMedia.ref) ||
    referenceMedia.parent !== null || currentMedia.parent !== null ||
    (referenceMedia.kind !== "image" && referenceMedia.kind !== "video") ||
    currentMedia.kind !== referenceMedia.kind ||
    !["completed", "partial"].includes(reference.snapshot.runtime_status) ||
    !["completed", "partial"].includes(current.snapshot.runtime_status) ||
    reference.snapshot.findings.some((finding) => finding.task_id !== reference.snapshot.task_id) ||
    current.snapshot.findings.some((finding) => finding.task_id !== current.snapshot.task_id)
  ) return false;
  return value.rows.every((row, index) => {
    if (!isRecord(row) || !hasExactFields(row, COMPARISON_ROW_FIELDS) ||
        row.label !== COMPARISON_LABELS[index]) return false;
    const referenceCounts = comparisonCounts(reference, row.label as string);
    const currentCounts = comparisonCounts(current, row.label as string);
    return isStatusCounts(row.reference, referenceCounts) &&
      isStatusCounts(row.current, currentCounts) &&
      Number.isInteger(row.confirmed_delta) &&
      row.confirmed_delta === currentCounts.confirmed - referenceCounts.confirmed;
  });
}
