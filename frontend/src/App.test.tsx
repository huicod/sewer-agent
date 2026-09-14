import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import { isTaskRecord } from "./validators";

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
  it("validates optional parent links and resumes the linked review task", async () => {
    const parentId = "55555555-5555-4555-8555-555555555555";
    const child = { ...task(), parent_task_id: parentId };
    expect(isTaskRecord(task())).toBe(true);
    expect(isTaskRecord({ ...task(), parent_task_id: null })).toBe(true);
    expect(isTaskRecord(child)).toBe(true);
    expect(isTaskRecord({ ...task(), parent_task_id: "javascript:bad" })).toBe(false);
    expect(isTaskRecord({ ...task(), parent_task_id: taskId })).toBe(false);
    localStorage.setItem("pipe-inspector.session-id", sessionId);
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ session: { session_id: sessionId, created_at: createdAt }, tasks: [child] }))
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValueOnce(jsonResponse({ ...task("running"), parent_task_id: parentId }, 202));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<App pollIntervalMs={60_000} />);
    await user.click(await screen.findByRole("button", { name: "启动关联复检" }));
    expect(await screen.findByText("关联复检正在执行（不重新检测）")).toBeTruthy();
    expect(screen.getByRole("link", { name: "原任务报告" }).getAttribute("href")).toBe(`/api/tasks/${parentId}/report?format=html`);
    expect(fetchMock.mock.calls[2]?.[0]).toBe(`/api/tasks/${taskId}/start`);
  });

  it("accepts only boolean can_cancel values when the optional field is present", () => {
    expect(isTaskRecord(task())).toBe(true);
    expect(isTaskRecord({
      ...task("running"),
      snapshot: { ...task("running").snapshot, can_cancel: true },
    })).toBe(true);
    expect(isTaskRecord({
      ...task("running"),
      snapshot: { ...task("running").snapshot, can_cancel: "yes" },
    })).toBe(false);
  });

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

    expect(await screen.findByText("已上传并入队，等待启动检查")).toBeTruthy();
    expect(screen.getByText("0%")) .toBeTruthy();
    expect(screen.queryByRole("button", { name: "取消检查" })).toBeNull();
    expect(localStorage.getItem("pipe-inspector.session-id")).toBe(sessionId);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("starts only the existing task and displays the returned inspection result", async () => {
    localStorage.setItem("pipe-inspector.session-id", sessionId);
    const running = {
      ...task("running"),
      snapshot: {
        ...task("running").snapshot,
        stage: "detecting",
        stage_progress: 0.4,
        overall_progress: 0.35,
        active_tool: "yolo-primary",
        retry_count: 1,
      },
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(
        jsonResponse({
          session: { session_id: sessionId, created_at: createdAt },
          tasks: [task()],
        }),
      )
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValueOnce(jsonResponse(running, 202));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(<App pollIntervalMs={60_000} />);
    await user.click(await screen.findByRole("button", { name: "启动检查" }));

    expect(await screen.findByText("detecting")).toBeTruthy();
    expect(screen.getByText("yolo-primary")).toBeTruthy();
    expect(screen.getByText("待定")).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(fetchMock.mock.calls[2]).toEqual([
      "/api/tasks/" + taskId + "/start",
      { method: "POST" },
    ]);
    expect(fetchMock.mock.calls.some(([url]) => url === "/api/assets")).toBe(false);
    expect(fetchMock.mock.calls.some(([url]) => url === "/api/tasks")).toBe(false);
  });

  it("names the active enhancement stage without fetching every task detail", async () => {
    localStorage.setItem("pipe-inspector.session-id", sessionId);
    const enhancing = {
      ...task("running"),
      snapshot: {
        ...task("running").snapshot,
        stage: "tools",
        stage_progress: 0.5,
        overall_progress: 0.75,
        active_tool: "dehaze",
      },
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({
        session: { session_id: sessionId, created_at: createdAt },
        tasks: [enhancing],
      }))
      .mockResolvedValueOnce(jsonResponse([]));
    vi.stubGlobal("fetch", fetchMock);

    render(<App pollIntervalMs={60_000} />);

    expect(await screen.findByText("图片增强处理")).toBeTruthy();
    expect(screen.getByText("去雾增强")).toBeTruthy();
    expect(screen.getByRole("button", { name: "展开图片增强对照" })).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it.each([
    { stage: "tools", tool: "enhancement_selection", label: "按需选择图片增强" },
    { stage: "verifying", tool: "video_scene", label: "视频场景筛选" },
  ])("names $tool without fetching task detail", async ({ stage, tool, label }) => {
    localStorage.setItem("pipe-inspector.session-id", sessionId);
    const selecting = {
      ...task("running"),
      snapshot: {
        ...task("running").snapshot,
        stage,
        stage_progress: 0.5,
        overall_progress: 0.7,
        active_tool: tool,
      },
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({
        session: { session_id: sessionId, created_at: createdAt },
        tasks: [selecting],
      }))
      .mockResolvedValueOnce(jsonResponse([]));
    vi.stubGlobal("fetch", fetchMock);

    render(<App pollIntervalMs={60_000} />);

    expect(await screen.findByText(label)).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("offers a natural-language adaptive enhancement example", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ session_id: sessionId, created_at: createdAt }, 201))
      .mockResolvedValueOnce(jsonResponse([]));
    vi.stubGlobal("fetch", fetchMock);

    render(<App pollIntervalMs={60_000} />);

    expect(await screen.findByText(/必要时按需增强后再检查/)).toBeTruthy();
    expect(screen.getByText(/对新图或关联原图/)).toBeTruthy();
  });

  it("shows a safe start failure and keeps the same task available for retry", async () => {
    localStorage.setItem("pipe-inspector.session-id", sessionId);
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(
        jsonResponse({
          session: { session_id: sessionId, created_at: createdAt },
          tasks: [task()],
        }),
      )
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValueOnce(jsonResponse({ detail: { message: "图片检查服务暂不可用" } }, 503));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(<App pollIntervalMs={60_000} />);
    await user.click(await screen.findByRole("button", { name: "启动检查" }));

    expect((await screen.findByRole("alert")).textContent).toContain("图片检查服务暂不可用");
    expect(screen.queryByText("正常")).toBeNull();
    expect(screen.getByRole("button", { name: "启动检查" })).toBeTruthy();
  });

  it("shows video progress and timestamped evidence, then stores the cancellation result", async () => {
    localStorage.setItem("pipe-inspector.session-id", sessionId);
    const videoTask = {
      ...task("running"),
      snapshot: {
        ...task("running").snapshot,
        can_cancel: true,
        stage: "video_scan",
        stage_progress: 0.5,
        overall_progress: 0.4,
        processed_frames: 24,
        processed_chunks: 3,
        findings: [{
          finding_id: "55555555-5555-4555-8555-555555555555",
          task_id: taskId,
          canonical_label: "AJ",
          open_label: null,
          review_status: "tentative",
          source: "detector",
          score: 0.8,
          evidence: [{
            asset: asset.ref,
            region: null,
            frame_index: 12,
            timestamp_seconds: 1.25,
          }],
          confirmed_by: null,
        }],
      },
    };
    const cancelled = {
      ...videoTask,
      snapshot: {
        ...videoTask.snapshot,
        runtime_status: "cancelled",
        can_cancel: false,
      },
    };
    let resolveCancel!: (response: Response) => void;
    const cancelResponse = new Promise<Response>((resolve) => {
      resolveCancel = resolve;
    });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({
        session: { session_id: sessionId, created_at: createdAt },
        tasks: [videoTask],
      }))
      .mockResolvedValueOnce(jsonResponse([]))
      .mockReturnValueOnce(cancelResponse);
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(<App pollIntervalMs={60_000} />);

    expect(await screen.findByText("24")).toBeTruthy();
    expect(screen.getByText("3")).toBeTruthy();
    expect(screen.getByRole("link", { name: "原始证据 1 · 1.25 秒" })).toBeTruthy();
    const cancelButton = screen.getByRole("button", { name: "取消检查" });
    await user.click(cancelButton);
    expect((cancelButton as HTMLButtonElement).disabled).toBe(true);
    expect(fetchMock.mock.calls[2]).toEqual([
      `/api/tasks/${taskId}/cancel`,
      { method: "POST" },
    ]);

    resolveCancel(jsonResponse(cancelled));
    expect(await screen.findByText("已取消")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "取消检查" })).toBeNull();
  });

  it("keeps cancellation available after a request failure", async () => {
    localStorage.setItem("pipe-inspector.session-id", sessionId);
    const videoTask = {
      ...task("running"),
      snapshot: { ...task("running").snapshot, can_cancel: true },
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({
        session: { session_id: sessionId, created_at: createdAt },
        tasks: [videoTask],
      }))
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValueOnce(jsonResponse({ detail: { message: "取消服务暂不可用" } }, 503));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(<App pollIntervalMs={60_000} />);
    await user.click(await screen.findByRole("button", { name: "取消检查" }));

    expect((await screen.findByRole("alert")).textContent).toContain("取消服务暂不可用");
    expect((screen.getByRole("button", { name: "取消检查" }) as HTMLButtonElement).disabled).toBe(false);
    expect(screen.queryByText("已取消")).toBeNull();
  });

  it("recovers the saved session and its tasks after reload", async () => {
    localStorage.setItem("pipe-inspector.session-id", sessionId);
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({
        session: { session_id: sessionId, created_at: createdAt },
        tasks: [task()],
      }))
      .mockResolvedValueOnce(jsonResponse([]));
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
      .mockResolvedValueOnce(jsonResponse([]))
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
    expect(screen.getByText("0 条")).toBeTruthy();
    expect(screen.queryByText("服务器正在处理这条消息。")).toBeNull();
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
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValue(jsonResponse(task()));
    vi.stubGlobal("fetch", queuedFetch);
    const queuedView = render(<App pollIntervalMs={10} />);
    await waitFor(() => {
      expect(queuedFetch.mock.calls.some(([url]) => url === `/api/tasks/${taskId}`)).toBe(true);
    });
    queuedView.unmount();

    const terminalFetch = vi.fn()
      .mockResolvedValueOnce(jsonResponse({
        session: { session_id: sessionId, created_at: createdAt },
        tasks: [task("completed")],
      }))
      .mockResolvedValueOnce(jsonResponse([]));
    vi.stubGlobal("fetch", terminalFetch);
    render(<App pollIntervalMs={10} />);
    await screen.findByText("检查破损");
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(terminalFetch).toHaveBeenCalledTimes(2);
  });
});
