import type {
  AssetRecord,
  AssetRef,
  Finding,
  RuntimeStatus,
  SessionDetail,
  SessionRecord,
  TaskRecord,
  TaskSnapshot,
  TurnRecord,
  TurnStage,
  TurnStatus,
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

function isTaskSnapshot(value: unknown): value is TaskSnapshot {
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
    isTaskSnapshot(value.snapshot)
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
