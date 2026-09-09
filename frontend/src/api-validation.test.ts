import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, createSession, getSession, startTask, uploadAsset } from "./api";

function success(payload: unknown) {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("successful response validation", () => {
  it("rejects a malformed session record at the API boundary", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(success({ session_id: 17, created_at: "2026-09-09T09:00:00Z" })),
    );

    await expect(createSession()).rejects.toEqual(
      expect.objectContaining<ApiError>({
        name: "ApiError",
        status: 200,
        message: "服务返回的数据格式无效",
      }),
    );
  });

  it("rejects a malformed asset reference at the API boundary", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        success({
          ref: { asset_id: "asset", version: 0 },
          kind: "image",
          media_type: "image/jpeg",
          byte_size: 3,
          width: 32,
          height: 24,
          duration_seconds: null,
          parent: null,
        }),
      ),
    );

    await expect(uploadAsset(new File(["raw"], "evidence.jpg"))).rejects.toEqual(
      expect.objectContaining<ApiError>({
        name: "ApiError",
        status: 200,
        message: "服务返回的数据格式无效",
      }),
    );
  });

  it("rejects malformed task state nested in a session detail", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        success({
          session: {
            session_id: "33333333-3333-4333-8333-333333333333",
            created_at: "2026-09-09T09:00:00Z",
          },
          tasks: [
            {
              request_id: "22222222-2222-4222-8222-222222222222",
              session_id: "33333333-3333-4333-8333-333333333333",
              input_asset: {
                asset_id: "11111111-1111-4111-8111-111111111111",
                version: 1,
              },
              instruction: "检查破损",
              created_at: "2026-09-09T09:00:00Z",
              snapshot: {
                schema_version: "1",
                task_id: "44444444-4444-4444-8444-444444444444",
                runtime_status: "made_up",
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
              },
            },
          ],
        }),
      ),
    );

    await expect(getSession("33333333-3333-4333-8333-333333333333")).rejects.toEqual(
      expect.objectContaining<ApiError>({
        name: "ApiError",
        status: 200,
        message: "服务返回的数据格式无效",
      }),
    );
  });

  it("rejects malformed findings returned when starting inspection", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        success({
          request_id: "22222222-2222-4222-8222-222222222222",
          session_id: "33333333-3333-4333-8333-333333333333",
          input_asset: {
            asset_id: "11111111-1111-4111-8111-111111111111",
            version: 1,
          },
          instruction: "检查破损",
          created_at: "2026-09-09T09:00:00Z",
          snapshot: {
            schema_version: "1",
            task_id: "44444444-4444-4444-8444-444444444444",
            runtime_status: "partial",
            inspection_result: "inconclusive",
            stage: "finished",
            stage_progress: 1,
            overall_progress: 1,
            processed_frames: 1,
            processed_chunks: 0,
            active_tool: null,
            retry_count: 0,
            waiting_reason: null,
            updated_at: "2026-09-09T09:00:00Z",
            findings: [{ canonical_label: "NOT_A_LABEL" }],
            coverage: {
              sufficient_coverage: true,
              sufficient_quality: true,
              negative_audit_complete: true,
            },
          },
        }),
      ),
    );

    await expect(startTask("44444444-4444-4444-8444-444444444444")).rejects.toEqual(
      expect.objectContaining<ApiError>({ name: "ApiError", status: 200, message: "服务返回的数据格式无效" }),
    );
  });
  it("rejects an unknown inspection result", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        success({
          request_id: "22222222-2222-4222-8222-222222222222",
          session_id: "33333333-3333-4333-8333-333333333333",
          input_asset: {
            asset_id: "11111111-1111-4111-8111-111111111111",
            version: 1,
          },
          instruction: "检查破损",
          created_at: "2026-09-09T09:00:00Z",
          snapshot: {
            schema_version: "1",
            task_id: "44444444-4444-4444-8444-444444444444",
            runtime_status: "running",
            inspection_result: "made_up",
            stage: "detecting",
            stage_progress: 0.5,
            overall_progress: 0.4,
            processed_frames: 1,
            processed_chunks: 0,
            active_tool: "yolo-primary",
            retry_count: 0,
            waiting_reason: null,
            updated_at: "2026-09-09T09:00:00Z",
            findings: [],
            coverage: {
              sufficient_coverage: false,
              sufficient_quality: false,
              negative_audit_complete: false,
            },
          },
        }),
      ),
    );

    await expect(startTask("44444444-4444-4444-8444-444444444444")).rejects.toEqual(
      expect.objectContaining<ApiError>({
        name: "ApiError",
        status: 200,
        message: "服务返回的数据格式无效",
      }),
    );
  });
});
