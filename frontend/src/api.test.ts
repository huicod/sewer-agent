import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, cancelTask, createTask, createTurn, getTurn, listTurns, uploadAsset } from "./api";

const asset = {
  ref: { asset_id: "11111111-1111-4111-8111-111111111111", version: 1 },
  kind: "image",
  media_type: "image/jpeg",
  byte_size: 3,
  width: 32,
  height: 24,
  duration_seconds: null,
  parent: null,
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("intake API boundary", () => {
  it("uploads the selected bytes using the required multipart field", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(asset), {
        status: 201,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const file = new File([new Uint8Array([1, 2, 3])], "现场图.jpg", {
      type: "image/jpeg",
    });

    await expect(uploadAsset(file)).resolves.toEqual(asset);
    const [url, request] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/assets");
    expect(request.method).toBe("POST");
    expect(request.body).toBeInstanceOf(FormData);
    expect((request.body as FormData).get("file")).toBe(file);
  });

  it("submits the idempotency and asset references as JSON", async () => {
    const task = {
      request_id: "22222222-2222-4222-8222-222222222222",
      session_id: "33333333-3333-4333-8333-333333333333",
      input_asset: asset.ref,
      instruction: "检查破损",
      created_at: "2026-09-09T09:00:00Z",
      snapshot: {
        schema_version: "1",
        task_id: "44444444-4444-4444-8444-444444444444",
        runtime_status: "queued",
        inspection_result: "pending",
        stage: "queued",
        stage_progress: 0,
        overall_progress: 0,
        processed_frames: 0,
        processed_chunks: 0,
        active_tool: null,
        retry_count: 0,
        waiting_reason: null,
        updated_at: "2026-09-09T09:00:00Z",
        findings: [],
        coverage: { sufficient_coverage: false, sufficient_quality: false, negative_audit_complete: false },
      },
    };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(task), {
        status: 201,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await createTask({
      request_id: task.request_id,
      session_id: task.session_id,
      input_asset: asset.ref,
      instruction: task.instruction,
      inspection_mode: "yolo_vlm",
    });

    const [url, request] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/tasks");
    expect(request.method).toBe("POST");
    expect(request.headers).toEqual({ "content-type": "application/json" });
    expect(JSON.parse(String(request.body))).toEqual({
      request_id: task.request_id,
      session_id: task.session_id,
      input_asset: asset.ref,
      instruction: "检查破损",
      inspection_mode: "yolo_vlm",
    });
  });

  it("surfaces the safe application error message", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail: { code: "unsupported_media", message: "文件格式不受支持" },
          }),
          { status: 415, headers: { "content-type": "application/json" } },
        ),
      ),
    );

    await expect(uploadAsset(new File(["bad"], "bad.txt"))).rejects.toEqual(
      expect.objectContaining<ApiError>({
        name: "ApiError",
        status: 415,
        message: "文件格式不受支持",
      }),
    );
  });

  it("posts cancellation and validates the returned task record", async () => {
    const cancelled = {
      request_id: "22222222-2222-4222-8222-222222222222",
      session_id: "33333333-3333-4333-8333-333333333333",
      input_asset: asset.ref,
      instruction: "检查破损",
      created_at: "2026-09-09T09:00:00Z",
      snapshot: {
        schema_version: "1",
        task_id: "44444444-4444-4444-8444-444444444444",
        runtime_status: "cancelled",
        inspection_result: "pending",
        stage: "cancelled",
        stage_progress: 0.5,
        overall_progress: 0.4,
        processed_frames: 24,
        processed_chunks: 3,
        active_tool: null,
        retry_count: 0,
        waiting_reason: null,
        updated_at: "2026-09-09T09:00:00Z",
        can_cancel: false,
        findings: [],
        coverage: { sufficient_coverage: false, sufficient_quality: false, negative_audit_complete: false },
      },
    };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(cancelled), { status: 200, headers: { "content-type": "application/json" } }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(cancelTask(cancelled.snapshot.task_id)).resolves.toEqual(cancelled);
    expect(fetchMock).toHaveBeenCalledWith(`/api/tasks/${cancelled.snapshot.task_id}/cancel`, { method: "POST" });
  });

  it("rejects a malformed cancellation response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ snapshot: { can_cancel: "yes" } }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    ));

    await expect(cancelTask("44444444-4444-4444-8444-444444444444")).rejects.toEqual(
      expect.objectContaining<ApiError>({ name: "ApiError", status: 200, message: "服务返回的数据格式无效" }),
    );
  });
});

describe("conversation API boundary", () => {
  const sessionId = "33333333-3333-4333-8333-333333333333";
  const requestId = "22222222-2222-4222-8222-222222222222";
  const turn = {
    request_id: requestId,
    session_id: sessionId,
    text: "检查附件",
    input_asset: asset.ref,
    task_id: null,
    created_at: "2026-09-09T09:00:00Z",
    updated_at: "2026-09-09T09:00:00Z",
    status: "queued",
    stage: "queued",
    reply: null,
    linked_task_id: null,
    error_code: null,
    attempts: 0,
  };

  it("submits the exact durable turn identity and intent", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(turn), { status: 202, headers: { "content-type": "application/json" } }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await createTurn(sessionId, {
      request_id: requestId,
      text: "检查附件",
      input_asset: asset.ref,
      task_id: null,
      inspection_mode: "yolo_vlm",
    });

    expect(fetchMock).toHaveBeenCalledWith(`/api/sessions/${sessionId}/turns`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        request_id: requestId,
        text: "检查附件",
        input_asset: asset.ref,
        task_id: null,
        inspection_mode: "yolo_vlm",
      }),
    });
  });

  it("uses the collection and identity URLs for restoring and polling turns", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify([turn]), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(turn), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await listTurns(sessionId);
    await getTurn(sessionId, requestId);

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      `/api/sessions/${sessionId}/turns`,
      `/api/sessions/${sessionId}/turns/${requestId}`,
    ]);
  });
});
