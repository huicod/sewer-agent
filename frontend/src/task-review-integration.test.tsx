import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./test/InspectionWorkbench";

const sessionId = "33333333-3333-4333-8333-333333333333";
const taskId = "44444444-4444-4444-8444-444444444444";
const assetId = "11111111-1111-4111-8111-111111111111";
const findingId = "55555555-5555-4555-8555-555555555555";
const createdAt = "2026-09-11T12:00:00Z";

function task(runtime_status: "waiting_review" | "completed" = "waiting_review") {
  return {
    request_id: "22222222-2222-4222-8222-222222222222",
    session_id: sessionId,
    input_asset: { asset_id: assetId, version: 1 },
    instruction: "检查视频",
    created_at: createdAt,
    snapshot: {
      schema_version: "1",
      task_id: taskId,
      runtime_status,
      inspection_result: "inconclusive",
      stage: runtime_status === "waiting_review" ? "human_review" : "finished",
      stage_progress: runtime_status === "waiting_review" ? 0 : 1,
      overall_progress: runtime_status === "waiting_review" ? 0.95 : 1,
      processed_frames: 20,
      processed_chunks: 2,
      can_cancel: runtime_status === "waiting_review",
      active_tool: null,
      retry_count: 0,
      waiting_reason: runtime_status === "waiting_review" ? "需要人工确认" : null,
      updated_at: createdAt,
      findings: [{
        finding_id: findingId,
        task_id: taskId,
        canonical_label: "BX",
        open_label: null,
        review_status: runtime_status === "waiting_review" ? "needs_review" : "rejected",
        source: runtime_status === "waiting_review" ? "detector" : "human",
        score: 0.7,
        evidence: [{
          asset: { asset_id: assetId, version: 1 },
          region: { width: 640, height: 480, bbox_xyxy: [10, 20, 100, 120] },
          frame_index: 8,
          timestamp_seconds: 2.5,
        }],
        confirmed_by: null,
      }],
      coverage: { sufficient_coverage: true, sufficient_quality: true, negative_audit_complete: false },
    },
  };
}

function response(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), { status, headers: { "content-type": "application/json" } });
}

beforeEach(() => {
  localStorage.setItem("pipe-inspector.session-id", sessionId);
});

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("TaskCard review and evidence integration", () => {
  it("restores waiting state but loads review only when the reviewer opens it", async () => {
    const waiting = task();
    const detail = { task_id: taskId, can_review: true, snapshot: waiting.snapshot, record: null };
    const fetchMock = vi.fn(async (url: string) => {
      if (url === `/api/sessions/${sessionId}`) return response({ session: { session_id: sessionId, created_at: createdAt }, tasks: [waiting] });
      if (url === `/api/sessions/${sessionId}/turns`) return response([]);
      if (url === `/api/tasks/${taskId}/review`) return response(detail);
      throw new Error(`unexpected URL ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(<App pollIntervalMs={60_000} />);
    const details = await screen.findByRole("complementary", { name: "任务与证据" });
    await within(details).findByText("等待人工复核");
    expect(fetchMock.mock.calls.some(([url]) => url === `/api/tasks/${taskId}/review`)).toBe(false);

    await user.click(screen.getByRole("button", { name: "开始人工复核" }));
    await screen.findByRole("heading", { name: "人工复核" });
    expect(fetchMock.mock.calls.filter(([url]) => url === `/api/tasks/${taskId}/review`)).toHaveLength(1);
    expect(screen.queryByText("未解决项尚无人工处理入口")).toBeNull();
  });

  it("opens terminal audit history and accepts a valid null legacy record", async () => {
    const terminal = task("completed");
    const fetchMock = vi.fn(async (url: string) => {
      if (url === `/api/sessions/${sessionId}`) return response({ session: { session_id: sessionId, created_at: createdAt }, tasks: [terminal] });
      if (url === `/api/sessions/${sessionId}/turns`) return response([]);
      if (url === `/api/tasks/${taskId}/review`) return response({ task_id: taskId, can_review: false, snapshot: terminal.snapshot, record: null });
      throw new Error(`unexpected URL ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(<App pollIntervalMs={60_000} />);
    await user.click(await screen.findByRole("button", { name: "查看审核记录" }));

    expect(await screen.findByText("该历史任务没有可用的结构化人工审核记录。")).toBeTruthy();
  });

  it("offers the lazy video timeline when a source frame locator proves the task is video", async () => {
    const waiting = task();
    const fetchMock = vi.fn(async (url: string) => {
      if (url === `/api/sessions/${sessionId}`) return response({ session: { session_id: sessionId, created_at: createdAt }, tasks: [waiting] });
      if (url === `/api/sessions/${sessionId}/turns`) return response([]);
      if (url === `/api/tasks/${taskId}/video`) return response({ task_id: taskId, scan: null, evidence: null });
      throw new Error(`unexpected URL ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(<App pollIntervalMs={60_000} />);
    const timelineButton = await screen.findByRole("button", { name: "展开视频候选时间线" });
    expect(fetchMock.mock.calls.some(([url]) => url === `/api/tasks/${taskId}/video`)).toBe(false);
    await user.click(timelineButton);
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => url === `/api/tasks/${taskId}/video`)).toBe(true));
  });

  it("previews the exact saved evidence frame and links the original video separately", async () => {
    const waiting = task();
    const frameId = "66666666-6666-4666-8666-666666666666";
    waiting.snapshot.findings[0].evidence[0].asset = { asset_id: frameId, version: 2 };
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url === `/api/sessions/${sessionId}`) return response({ session: { session_id: sessionId, created_at: createdAt }, tasks: [waiting] });
      if (url === `/api/sessions/${sessionId}/turns`) return response([]);
      throw new Error(`unexpected URL ${url}`);
    }));
    render(<App pollIntervalMs={60_000} />);
    const link = await screen.findByRole("link", { name: "原始证据 1 · 2.5 秒" });
    const frameUrl = `/api/assets/${frameId}/versions/2/content`;
    expect(link.getAttribute("href")).toBe(frameUrl);
    expect(link.querySelector("image")?.getAttribute("href")).toBe(frameUrl);
    expect(link.querySelector("rect")?.getAttribute("width")).toBe("90");
    expect(screen.getAllByRole("link", { name: "打开输入文件" })[0].getAttribute("href")).toBe(`/api/assets/${assetId}/versions/1/content`);
  });

  it("automatically previews the selected no-finding video from source metadata", async () => {
    vi.stubGlobal("IntersectionObserver", undefined);
    const videoTask = task();
    videoTask.snapshot.findings = [];
    const fetchMock = vi.fn(async (url: string) => {
      if (url === `/api/sessions/${sessionId}`) return response({ session: { session_id: sessionId, created_at: createdAt }, tasks: [videoTask] });
      if (url === `/api/sessions/${sessionId}/turns`) return response([]);
      if (url === `/api/assets/${assetId}/versions/1`) return response({
        ref: videoTask.input_asset, kind: "video", media_type: "video/mp4", byte_size: 2048,
        width: 640, height: 480, duration_seconds: 8.5, parent: null,
      });
      throw new Error(`unexpected URL ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App pollIntervalMs={60_000} />);
    const video = await screen.findByLabelText("输入视频预览");
    expect(video.getAttribute("src")).toBe(`/api/assets/${assetId}/versions/1/content`);
    expect(screen.getByRole("link", { name: "打开原视频" }).getAttribute("href")).toBe(`/api/assets/${assetId}/versions/1/content`);

    expect(await screen.findByRole("button", { name: "展开视频候选时间线" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "展开检查依据" })).toBeNull();
  });
});
