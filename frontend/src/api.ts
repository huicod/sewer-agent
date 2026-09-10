import type {
  AssetRecord,
  SessionDetail,
  SessionRecord,
  TaskRecord,
  TaskSubmission,
  TurnRecord,
  TurnSubmission,
} from "./types";
import {
  isAssetRecord,
  isSessionDetail,
  isSessionRecord,
  isTaskRecord,
  isTurnList,
  isTurnRecord,
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

export function getSession(sessionId: string): Promise<SessionDetail> {
  return requestJson<SessionDetail>(`/api/sessions/${encodeURIComponent(sessionId)}`, isSessionDetail);
}

export function uploadAsset(file: File): Promise<AssetRecord> {
  const body = new FormData();
  body.append("file", file);
  return requestJson<AssetRecord>("/api/assets", isAssetRecord, { method: "POST", body });
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

export function getTask(taskId: string): Promise<TaskRecord> {
  return requestJson<TaskRecord>(`/api/tasks/${encodeURIComponent(taskId)}`, isTaskRecord);
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
