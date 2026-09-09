import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";

const sessionId = "33333333-3333-4333-8333-333333333333";
const createdAt = "2026-09-09T09:00:00Z";

function asset(assetId: string) {
  return {
    ref: { asset_id: assetId, version: 1 },
    kind: "image",
    media_type: "image/jpeg",
    byte_size: 3,
    width: 32,
    height: 24,
    duration_seconds: null,
    parent: null,
  };
}

function task(assetId: string) {
  return {
    request_id: "66666666-6666-4666-8666-666666666666",
    session_id: sessionId,
    input_asset: { asset_id: assetId, version: 1 },
    instruction: "检查破损",
    created_at: createdAt,
    snapshot: {
      schema_version: "1",
      task_id: "77777777-7777-4777-8777-777777777777",
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
      updated_at: createdAt,
    },
  };
}

function response(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}

beforeEach(() => {
  localStorage.setItem("pipe-inspector.session-id", sessionId);
});

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("task submission retry identity", () => {
  it("uploads a different File object even when its metadata matches a failed submission", async () => {
    const firstAssetId = "11111111-1111-4111-8111-111111111111";
    const secondAssetId = "55555555-5555-4555-8555-555555555555";
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        response({
          session: { session_id: sessionId, created_at: createdAt },
          tasks: [],
        }),
      )
      .mockResolvedValueOnce(response(asset(firstAssetId), 201))
      .mockResolvedValueOnce(
        response(
          { detail: { code: "service_unavailable", message: "任务服务暂不可用" } },
          503,
        ),
      )
      .mockResolvedValueOnce(response(asset(secondAssetId), 201))
      .mockResolvedValueOnce(response(task(secondAssetId), 201));
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(globalThis.crypto, "randomUUID")
      .mockReturnValueOnce("22222222-2222-4222-8222-222222222222")
      .mockReturnValueOnce("66666666-6666-4666-8666-666666666666");
    const user = userEvent.setup();
    const sameMetadata = {
      type: "image/jpeg",
      lastModified: 1_789_000_000_000,
    };
    const firstFile = new File([new Uint8Array([1, 2, 3])], "现场图.jpg", sameMetadata);
    const secondFile = new File([new Uint8Array([9, 8, 7])], "现场图.jpg", sameMetadata);

    render(<App pollIntervalMs={60_000} />);
    await screen.findByText(sessionId);
    await user.upload(screen.getByLabelText("现场文件"), firstFile);
    await user.type(screen.getByLabelText("检查要求"), "检查破损");
    await user.click(screen.getByRole("button", { name: "提交检查" }));
    expect((await screen.findByRole("alert")).textContent).toContain("任务服务暂不可用");

    await user.upload(screen.getByLabelText("现场文件"), secondFile);
    await user.click(screen.getByRole("button", { name: "提交检查" }));
    await screen.findByText("已接收入队，检查执行尚未接入");

    const uploadCalls = fetchMock.mock.calls.filter(([url]) => url === "/api/assets");
    expect(uploadCalls).toHaveLength(2);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(5));
    const secondTaskRequest = fetchMock.mock.calls[4]?.[1] as RequestInit;
    expect(JSON.parse(String(secondTaskRequest.body)).input_asset).toEqual({
      asset_id: secondAssetId,
      version: 1,
    });
  });
});
