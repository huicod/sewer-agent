import type {
  ApiConnectionTest,
  ApplicationSettingsUpdate,
  AssetRecord,
  ComparisonResult,
  EnhancementDetail,
  GeometryDetail,
  HumanReviewDetail,
  HumanReviewRecord,
  HumanReviewSubmission,
  ImageEvidenceDetail,
  SessionDetail,
  SessionRecord,
  SessionSummary,
  SessionUpdate,
  TaskRecord,
  TaskSubmission,
  TurnRecord,
  TurnSubmission,
  VideoDetail,
} from "./types";
import {
  isApplicationSettings,
  isApiConnectionResult,
  isAssetRecord,
  isComparisonResult,
  isEnhancementDetail,
  isExampleVideoList,
  isGeometryDetail,
  isHumanReviewDetail,
  isHumanReviewRecord,
  isImageEvidenceDetail,
  isSessionDetail,
  isSessionRecord,
  isSessionList,
  isSessionSummary,
  isTaskRecord,
  isTurnList,
  isTurnRecord,
  isVideoDetail,
} from "./validators";

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function errorMessage(status: number, payload: unknown): string {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = payload.detail;
    if (detail && typeof detail === "object" && "message" in detail) {
      const message = detail.message;
      if (typeof message === "string" && message.trim()) return message;
    }
    if (Array.isArray(detail)) {
      const messages = detail
        .map((item) => {
          if (!item || typeof item !== "object" || !("msg" in item)) return null;
          return typeof item.msg === "string" ? item.msg : null;
        })
        .filter((message): message is string => Boolean(message));
      if (messages.length) return `请求参数无效：${messages.join("；")}`;
    }
  }
  return `请求失败（${status}）`;
}

type Validator<T> = (payload: unknown) => payload is T;

async function requestJson<T>(
  url: string,
  validate: Validator<T>,
  init?: RequestInit,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url, init);
  } catch {
    throw new ApiError(0, "无法连接本地服务");
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new ApiError(response.status, "服务返回了无法解析的响应");
  }

  if (!response.ok) throw new ApiError(response.status, errorMessage(response.status, payload));
  if (!validate(payload)) throw new ApiError(response.status, "服务返回的数据格式无效");
  return payload;
}

export function createSession(): Promise<SessionRecord> {
  return requestJson<SessionRecord>("/api/sessions", isSessionRecord, { method: "POST" });
}

export function getApplicationSettings() {
  return requestJson("/api/settings", isApplicationSettings);
}

export function saveApplicationSettings(settings: ApplicationSettingsUpdate) {
  return requestJson("/api/settings", isApplicationSettings, {
    method: "PUT",
    headers: { "content-type": "application/json", "X-Sewer-Settings": "1" },
    body: JSON.stringify(settings),
  });
}

export function testApiConnection(settings: ApiConnectionTest) {
  return requestJson("/api/settings/test", isApiConnectionResult, {
    method: "POST",
    headers: { "content-type": "application/json", "X-Sewer-Settings": "1" },
    body: JSON.stringify(settings),
  });
}

export function listSessions(): Promise<SessionSummary[]> {
  return requestJson("/api/sessions", isSessionList);
}

export function updateSession(sessionId: string, update: SessionUpdate): Promise<SessionSummary> {
  return requestJson(`/api/sessions/${encodeURIComponent(sessionId)}`, isSessionSummary, {
    method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify(update),
  });
}

export function getSession(sessionId: string): Promise<SessionDetail> {
  return requestJson<SessionDetail>(`/api/sessions/${encodeURIComponent(sessionId)}`, isSessionDetail);
}

export function uploadAsset(file: File): Promise<AssetRecord> {
  const body = new FormData();
  body.append("file", file);
  return requestJson<AssetRecord>("/api/assets", isAssetRecord, { method: "POST", body });
}

export function listExampleVideos() {
  return requestJson("/api/example-videos", isExampleVideoList);
}

export function createExampleVideoAsset(id: string): Promise<AssetRecord> {
  return requestJson(`/api/example-videos/${encodeURIComponent(id)}/asset`,
    (asset): asset is AssetRecord => isAssetRecord(asset) && asset.kind === "video", { method: "POST" });
}

export function getAsset(ref: { asset_id: string; version: number }): Promise<AssetRecord> {
  return requestJson<AssetRecord>(
    `/api/assets/${encodeURIComponent(ref.asset_id)}/versions/${ref.version}`,
    isAssetRecord,
  );
}

export function createTask(submission: TaskSubmission): Promise<TaskRecord> {
  return requestJson<TaskRecord>("/api/tasks", isTaskRecord, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(submission),
  });
}

export function startTask(taskId: string): Promise<TaskRecord> {
  return requestJson<TaskRecord>(`/api/tasks/${encodeURIComponent(taskId)}/start`, isTaskRecord, { method: "POST" });
}

export function cancelTask(taskId: string): Promise<TaskRecord> {
  return requestJson<TaskRecord>(`/api/tasks/${encodeURIComponent(taskId)}/cancel`, isTaskRecord, { method: "POST" });
}

export function getTask(taskId: string): Promise<TaskRecord> {
  return requestJson<TaskRecord>(`/api/tasks/${encodeURIComponent(taskId)}`, isTaskRecord);
}

export function getReview(taskId: string): Promise<HumanReviewDetail> {
  return requestJson<HumanReviewDetail>(
    `/api/tasks/${encodeURIComponent(taskId)}/review`,
    isHumanReviewDetail,
  );
}

export function submitReview(
  taskId: string,
  submission: HumanReviewSubmission,
): Promise<HumanReviewRecord> {
  return requestJson<HumanReviewRecord>(
    `/api/tasks/${encodeURIComponent(taskId)}/review`,
    isHumanReviewRecord,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(submission),
    },
  );
}

export function getVideo(taskId: string): Promise<VideoDetail> {
  return requestJson<VideoDetail>(
    `/api/tasks/${encodeURIComponent(taskId)}/video`,
    isVideoDetail,
  );
}

export function getEnhancement(taskId: string): Promise<EnhancementDetail> {
  return requestJson<EnhancementDetail>(
    `/api/tasks/${encodeURIComponent(taskId)}/enhancement`,
    (payload): payload is EnhancementDetail =>
      isEnhancementDetail(payload) && payload.task_id === taskId,
  );
}

export function getGeometry(taskId: string): Promise<GeometryDetail> {
  return requestJson<GeometryDetail>(
    `/api/tasks/${encodeURIComponent(taskId)}/geometry`,
    (payload): payload is GeometryDetail =>
      isGeometryDetail(payload) && payload.task_id === taskId,
  );
}

export function getImageEvidence(taskId: string): Promise<ImageEvidenceDetail> {
  return requestJson<ImageEvidenceDetail>(
    `/api/tasks/${encodeURIComponent(taskId)}/image-evidence`,
    (payload): payload is ImageEvidenceDetail =>
      isImageEvidenceDetail(payload) && payload.task_id === taskId,
  );
}

export function createTurn(sessionId: string, submission: TurnSubmission): Promise<TurnRecord> {
  return requestJson<TurnRecord>(`/api/sessions/${encodeURIComponent(sessionId)}/turns`, isTurnRecord, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(submission),
  });
}

export function listTurns(sessionId: string): Promise<TurnRecord[]> {
  return requestJson<TurnRecord[]>(`/api/sessions/${encodeURIComponent(sessionId)}/turns`, isTurnList);
}

export function getTurn(sessionId: string, requestId: string): Promise<TurnRecord> {
  return requestJson<TurnRecord>(
    `/api/sessions/${encodeURIComponent(sessionId)}/turns/${encodeURIComponent(requestId)}`,
    isTurnRecord,
  );
}

export function getComparison(sessionId: string, requestId: string): Promise<ComparisonResult | null> {
  return requestJson<ComparisonResult | null>(
    `/api/sessions/${encodeURIComponent(sessionId)}/turns/${encodeURIComponent(requestId)}/comparison`,
    (payload): payload is ComparisonResult | null => payload === null || (
      isComparisonResult(payload) && payload.request_id === requestId &&
      payload.reference.session_id === sessionId && payload.current.session_id === sessionId
    ),
  );
}
