import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, createTask, uploadAsset } from "./api";

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
});
