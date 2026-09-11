import type {
  AssetRecord,
  AssetRef,
  Finding,
  FindingReview,
  HumanReviewDetail,
  HumanReviewRecord,
  HumanReviewSubmission,
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

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
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
  return (
    isRecord(value) &&
    isUuid(value.request_id) &&
    isUuid(value.session_id) &&
    isAssetRef(value.input_asset) &&
    isNonEmptyString(value.instruction) &&
    isTimestamp(value.created_at) &&
    isTaskSnapshot(value.snapshot) &&
    (value.parent_task_id === undefined || value.parent_task_id === null ||
      (isUuid(value.parent_task_id) && value.parent_task_id !== value.snapshot.task_id))
  );
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
    Object.keys(value).length === TURN_FIELDS.size &&
    Object.keys(value).every((key) => TURN_FIELDS.has(key)) &&
    isUuid(value.request_id) &&
    isUuid(value.session_id) &&
    typeof value.text === "string" &&
    value.text.length >= 1 && value.text.length <= 4_000 &&
    (value.input_asset === null || isAssetRef(value.input_asset)) &&
    (value.task_id === null || isUuid(value.task_id)) &&
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

function isVideoEvidence(value: unknown): boolean {
  if (!isRecord(value) || !Array.isArray(value.frames) || !Array.isArray(value.review_frames)) return false;
  return value.frames.every((frame) =>
    isRecord(frame) && isNonNegativeInteger(frame.frame_index) &&
    isNonNegativeFinite(frame.timestamp_seconds) && isAssetRef(frame.asset),
  ) && value.review_frames.every(isNonNegativeInteger);
}

export function isVideoDetail(value: unknown): value is VideoDetail {
  return isRecord(value) && isUuid(value.task_id) &&
    (value.scan === null || isVideoScan(value.scan)) &&
    (value.evidence === null || isVideoEvidence(value.evidence));
}
