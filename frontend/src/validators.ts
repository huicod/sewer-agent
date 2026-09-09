import type {
  AssetRecord,
  AssetRef,
  RuntimeStatus,
  SessionDetail,
  SessionRecord,
  TaskRecord,
  TaskSnapshot,
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

function isTaskSnapshot(value: unknown): value is TaskSnapshot {
  return (
    isRecord(value) &&
    value.schema_version === "1" &&
    isUuid(value.task_id) &&
    typeof value.runtime_status === "string" &&
    RUNTIME_STATUSES.has(value.runtime_status as RuntimeStatus) &&
    isNonEmptyString(value.inspection_result) &&
    isNonEmptyString(value.stage) &&
    isProgress(value.stage_progress) &&
    isProgress(value.overall_progress) &&
    isNonNegativeInteger(value.processed_frames) &&
    isNonNegativeInteger(value.processed_chunks) &&
    isNullableString(value.active_tool) &&
    isNonNegativeInteger(value.retry_count) &&
    isNullableString(value.waiting_reason) &&
    isTimestamp(value.updated_at)
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
