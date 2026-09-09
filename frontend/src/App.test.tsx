import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";

const sessionId = "33333333-3333-4333-8333-333333333333";
const taskId = "44444444-4444-4444-8444-444444444444";
const requestId = "22222222-2222-4222-8222-222222222222";
const createdAt = "2026-09-09T09:00:00Z";

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

function task(runtimeStatus = "queued") {
  return {
    request_id: requestId,
    session_id: sessionId,
    input_asset: asset.ref,
    instruction: "检查破损",
    created_at: createdAt,
    snapshot: {
      schema_version: "1",
      task_id: taskId,
      runtime_status: runtimeStatus,
      inspection_result: runtimeStatus === "completed" ? "inconclusive" : "pending",
      stage: runtimeStatus === "completed" ? "finished" : "queued",
      stage_progress: runtimeStatus === "completed" ? 1 : 0,
      overall_progress: runtimeStatus === "completed" ? 1 : 0,
      processed_frames: 0,
      processed_chunks: 0,
      active_tool: null,
      retry_count: 0,
      waiting_reason: null,
      updated_at: createdAt,
      findings: [],
      coverage: {
        sufficient_coverage: false,
        sufficient_quality: false,
        negative_audit_complete: false,
      },
    },
  };
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("conversation-ready intake shell", () => {
  it("creates a session, uploads evidence and displays a truthful queued task", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ session_id: sessionId, created_at: createdAt }, 201))
      .mockResolvedValueOnce(jsonResponse(asset, 201))
      .mockResolvedValueOnce(jsonResponse(task(), 201));
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValue(requestId);
    const user = userEvent.setup();

    render(<App pollIntervalMs={60_000} />);
    await screen.findByText(sessionId);

    await user.upload(
      screen.getByLabelText("现场文件"),
      new File([new Uint8Array([1, 2, 3])], "现场图.jpg", { type: "image/jpeg" }),
    );
    await user.type(screen.getByLabelText("检查要求"), "检查破损");
    await user.click(screen.getByRole("button", { name: "提交检查" }));

    expect(await screen.findByText("已接收入队，检查执行尚未接入")).toBeTruthy();
    expect(screen.getByText("0%")) .toBeTruthy();
    expect(localStorage.getItem("pipe-inspector.session-id")).toBe(sessionId);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("recovers the saved session and its tasks after reload", async () => {
    localStorage.setItem("pipe-inspector.session-id", sessionId);
    const fetchMock = vi.fn().mockResolvedValueOnce(
      jsonResponse({
        session: { session_id: sessionId, created_at: createdAt },
        tasks: [task()],
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<App pollIntervalMs={60_000} />);

    expect(await screen.findByText("检查破损")).toBeTruthy();
    expect(fetchMock.mock.calls[0]?.[0]).toBe(`/api/sessions/${sessionId}`);
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });

  it("shows the server error without inventing an assistant response", async () => {
    localStorage.setItem("pipe-inspector.session-id", sessionId);
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({
          session: { session_id: sessionId, created_at: createdAt },
          tasks: [],
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse(
          { detail: { code: "unsupported_media", message: "文件格式不受支持" } },
          415,
        ),
      );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<App pollIntervalMs={60_000} />);
    await screen.findByText(sessionId);

    await user.upload(screen.getByLabelText("现场文件"), new File(["bad"], "bad.jpg", { type: "image/jpeg" }));
    await user.type(screen.getByLabelText("检查要求"), "检查这个文件");
    await user.click(screen.getByRole("button", { name: "提交检查" }));

    expect((await screen.findByRole("alert")).textContent).toContain("文件格式不受支持");
    expect(screen.queryByText(/助手/)).toBeNull();
  });

  it("polls queued work but stops polling terminal work", async () => {
    localStorage.setItem("pipe-inspector.session-id", sessionId);
    const queuedFetch = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({
          session: { session_id: sessionId, created_at: createdAt },
          tasks: [task()],
        }),
      )
      .mockResolvedValue(jsonResponse(task()));
    vi.stubGlobal("fetch", queuedFetch);
    const queuedView = render(<App pollIntervalMs={10} />);
    await waitFor(() => {
      expect(queuedFetch.mock.calls.some(([url]) => url === `/api/tasks/${taskId}`)).toBe(true);
    });
    queuedView.unmount();

    const terminalFetch = vi.fn().mockResolvedValueOnce(
      jsonResponse({
        session: { session_id: sessionId, created_at: createdAt },
        tasks: [task("completed")],
      }),
    );
    vi.stubGlobal("fetch", terminalFetch);
    render(<App pollIntervalMs={10} />);
    await screen.findByText("检查破损");
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(terminalFetch).toHaveBeenCalledTimes(1);
  });
});
