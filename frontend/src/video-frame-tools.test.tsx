import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import { VideoTimeline } from "./VideoTimeline";
import type { TaskRecord, VideoDetail } from "./types";
import { isTaskRecord } from "./validators";

const sessionId = "11111111-1111-4111-8111-111111111111";
const videoTaskId = "22222222-2222-4222-8222-222222222222";
const childTaskId = "33333333-3333-4333-8333-333333333333";
const regularTaskId = "44444444-4444-4444-8444-444444444444";
const videoAsset = { asset_id: "55555555-5555-4555-8555-555555555555", version: 1 };
const frameAsset = { asset_id: "66666666-6666-4666-8666-666666666666", version: 2 };
const createdAt = "2026-09-14T08:00:00Z";

function response(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function snapshot(taskId: string, withVideoLocator = false) {
  return {
    schema_version: "1" as const,
    task_id: taskId,
    runtime_status: "partial" as const,
    inspection_result: "inconclusive" as const,
    stage: "finished",
    stage_progress: 1,
    overall_progress: 1,
    processed_frames: withVideoLocator ? 30 : 1,
    processed_chunks: withVideoLocator ? 2 : 0,
    can_cancel: false,
    active_tool: null,
    retry_count: 0,
    waiting_reason: "仅保存了部分证据",
    updated_at: createdAt,
    findings: withVideoLocator ? [{
      finding_id: "77777777-7777-4777-8777-777777777777",
      task_id: taskId,
      canonical_label: "PL" as const,
      open_label: null,
      review_status: "needs_review" as const,
      source: "detector" as const,
      score: 0.7,
      evidence: [{
        asset: videoAsset,
        region: { width: 640, height: 480, bbox_xyxy: [10, 20, 110, 120] as [number, number, number, number] },
        frame_index: 30,
        timestamp_seconds: 6,
      }],
      confirmed_by: null,
    }] : [],
    coverage: {
      sufficient_coverage: false,
      sufficient_quality: true,
      negative_audit_complete: false,
    },
  };
}

function videoTask(): TaskRecord {
  return {
    parent_task_id: null,
    request_id: "88888888-8888-4888-8888-888888888888",
    session_id: sessionId,
    input_asset: videoAsset,
    instruction: "检查源视频",
    created_at: createdAt,
    snapshot: snapshot(videoTaskId, true),
  };
}

function regularTask(): TaskRecord {
  return {
    parent_task_id: null,
    request_id: "99999999-9999-4999-8999-999999999999",
    session_id: sessionId,
    input_asset: { asset_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", version: 1 },
    instruction: "检查普通图片",
    created_at: createdAt,
    snapshot: snapshot(regularTaskId),
  };
}

function frameChild(): TaskRecord {
  return {
    parent_task_id: videoTaskId,
    frame_source: {
      video_asset: videoAsset,
      frame_index: 30,
      timestamp_seconds: 6,
    },
    request_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    session_id: sessionId,
    input_asset: frameAsset,
    instruction: "增强这个低照度帧",
    created_at: createdAt,
    snapshot: snapshot(childTaskId),
  };
}

function videoDetail(): VideoDetail {
  return {
    task_id: videoTaskId,
    scan: null,
    evidence: {
      frames: [{ frame_index: 30, timestamp_seconds: 6, asset: frameAsset }],
      review_frames: [30],
    },
  };
}

function queuedTurn(requestId: string, text: string) {
  return {
    request_id: requestId,
    session_id: sessionId,
    text,
    input_asset: frameAsset,
    task_id: videoTaskId,
    created_at: createdAt,
    updated_at: createdAt,
    status: "queued",
    stage: "queued",
    reply: null,
    linked_task_id: null,
    error_code: null,
    attempts: 0,
  };
}

beforeEach(() => localStorage.setItem("pipe-inspector.session-id", sessionId));

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("video frame source validation", () => {
  it("accepts the exact optional source and remains compatible with older tasks", () => {
    expect(isTaskRecord(frameChild())).toBe(true);
    expect(isTaskRecord({ ...regularTask(), frame_source: null })).toBe(true);
    expect(isTaskRecord(regularTask())).toBe(true);
  });

  it.each([
    ["missing parent", () => ({ ...frameChild(), parent_task_id: null })],
    ["fractional frame", () => ({ ...frameChild(), frame_source: { ...frameChild().frame_source!, frame_index: 3.5 } })],
    ["negative frame", () => ({ ...frameChild(), frame_source: { ...frameChild().frame_source!, frame_index: -1 } })],
    ["infinite timestamp", () => ({ ...frameChild(), frame_source: { ...frameChild().frame_source!, timestamp_seconds: Infinity } })],
    ["negative timestamp", () => ({ ...frameChild(), frame_source: { ...frameChild().frame_source!, timestamp_seconds: -0.1 } })],
    ["bad video ref", () => ({ ...frameChild(), frame_source: { ...frameChild().frame_source!, video_asset: { ...videoAsset, version: 0 } } })],
    ["private extra field", () => ({ ...frameChild(), frame_source: { ...frameChild().frame_source!, server_path: "/srv/frame.jpg" } })],
  ])("rejects %s", (_name, makeTask) => {
    expect(isTaskRecord(makeTask())).toBe(false);
  });
});

describe("stored video evidence frame selection", () => {
  it("returns the exact persisted frame through the explicit callback", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(videoDetail())));
    const selected = vi.fn();
    const user = userEvent.setup();
    render(
      <VideoTimeline
        taskId={videoTaskId}
        snapshotRevision={createdAt}
        onSelectFrame={selected}
      />,
    );

    await user.click(screen.getByRole("button", { name: "展开视频候选时间线" }));
    const frameSummary = await screen.findByText("选择已存证据帧（1）");
    const frameDetails = frameSummary.closest("details") as HTMLDetailsElement;
    expect(frameDetails.open).toBe(false);
    await user.click(frameSummary);
    expect(frameDetails.open).toBe(true);
    await user.click(await screen.findByRole("button", { name: "选择证据帧 30 用于工具" }));

    expect(selected).toHaveBeenCalledWith({
      frame_index: 30,
      timestamp_seconds: 6,
      asset: frameAsset,
    });
  });

  it("submits parent video plus exact FRAME and keeps both fixed across retry", async () => {
    const requestId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
    const text = "请增强这个低照度帧";
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === `/api/sessions/${sessionId}`) {
        return response({ session: { session_id: sessionId, created_at: createdAt }, tasks: [videoTask()] });
      }
      if (url === `/api/sessions/${sessionId}/turns` && !init) return response([]);
      if (url === `/api/tasks/${videoTaskId}/video`) return response(videoDetail());
      if (url === `/api/sessions/${sessionId}/turns` && init?.method === "POST") {
        if (fetchMock.mock.calls.filter(([, callInit]) => callInit?.method === "POST").length === 1) {
          throw new TypeError("connection reset");
        }
        return response(queuedTurn(requestId, text), 202);
      }
      throw new Error(`unexpected ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValue(requestId);
    const user = userEvent.setup();
    render(<App pollIntervalMs={60_000} />);

    await user.click(await screen.findByRole("button", { name: "展开视频候选时间线" }));
    await user.click(await screen.findByText("选择已存证据帧（1）"));
    await user.click(await screen.findByRole("button", { name: "选择证据帧 30 用于工具" }));
    expect(screen.getByText("已选视频证据帧：帧 30 · 6 秒")).toBeTruthy();
    const conversationPanel = screen.getByRole("heading", { name: "检查助手" }).closest("section")!;
    const mode = conversationPanel.querySelector<HTMLSelectElement>('select[aria-label="检查模式"]')!;
    expect(mode.disabled).toBe(true);
    expect(mode.value).toBe("yolo_vlm");
    expect(conversationPanel.textContent).toContain("视频证据帧工具仅使用 YOLO＋VLM 复检");
    expect(screen.getByRole<HTMLAnchorElement>("link", { name: "打开已选帧" }).href)
      .toContain(`/api/assets/${frameAsset.asset_id}/versions/2/content`);
    await user.type(screen.getByLabelText("对话内容"), text);
    await user.click(screen.getByRole("button", { name: "发送消息" }));
    expect((await screen.findByRole("alert")).textContent).toContain("相同请求编号");
    await user.click(screen.getByRole("button", { name: "重新发送" }));

    const posts = fetchMock.mock.calls.filter(([, init]) => init?.method === "POST");
    expect(posts).toHaveLength(2);
    expect((posts[0][1] as RequestInit).body).toBe((posts[1][1] as RequestInit).body);
    expect(JSON.parse(String((posts[0][1] as RequestInit).body))).toEqual({
      request_id: requestId,
      text,
      input_asset: frameAsset,
      task_id: videoTaskId,
      inspection_mode: "yolo_vlm",
    });
    expect(fetchMock.mock.calls.some(([url]) => url === "/api/assets")).toBe(false);
  });

  it.each([
    ["a regular image task", [regularTaskId], regularTaskId],
    ["no linked task", [""], null],
    ["the original video after leaving it", [regularTaskId, videoTaskId], videoTaskId],
  ])("does not reuse a failed FRAME after choosing %s", async (_name, choices, expectedTaskId) => {
    const text = "请增强这个低照度帧";
    const firstRequestId = "12121212-1212-4121-8121-121212121212";
    const secondRequestId = "34343434-3434-4343-8343-343434343434";
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === `/api/sessions/${sessionId}`) {
        return response({
          session: { session_id: sessionId, created_at: createdAt },
          tasks: [videoTask(), regularTask()],
        });
      }
      if (url === `/api/sessions/${sessionId}/turns` && !init) return response([]);
      if (url === `/api/tasks/${videoTaskId}/video`) return response(videoDetail());
      if (url === `/api/sessions/${sessionId}/turns` && init?.method === "POST") {
        const posts = fetchMock.mock.calls.filter(([, callInit]) => callInit?.method === "POST");
        if (posts.length === 1) throw new TypeError("connection reset");
        const body = JSON.parse(String(init.body));
        return response({
          ...queuedTurn(body.request_id, text),
          input_asset: body.input_asset,
          task_id: body.task_id,
        }, 202);
      }
      throw new Error(`unexpected ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(globalThis.crypto, "randomUUID")
      .mockReturnValueOnce(firstRequestId)
      .mockReturnValueOnce(secondRequestId);
    const user = userEvent.setup();
    render(<App pollIntervalMs={60_000} />);

    await user.click(await screen.findByRole("button", { name: "展开视频候选时间线" }));
    await user.click(await screen.findByText("选择已存证据帧（1）"));
    await user.click(await screen.findByRole("button", { name: "选择证据帧 30 用于工具" }));
    await user.type(screen.getByLabelText("对话内容"), text);
    await user.click(screen.getByRole("button", { name: "发送消息" }));
    expect((await screen.findByRole("alert")).textContent).toContain("相同请求编号");

    for (const choice of choices) {
      await user.selectOptions(screen.getByLabelText("关联已有任务（可选）"), choice);
    }
    expect(screen.queryByText("已选视频证据帧：帧 30 · 6 秒")).toBeNull();
    await user.click(screen.getByRole("button", { name: "发送消息" }));

    await waitFor(() => {
      const posts = fetchMock.mock.calls.filter(([, init]) => init?.method === "POST");
      expect(posts).toHaveLength(2);
      expect(JSON.parse(String((posts[1][1] as RequestInit).body))).toEqual({
        request_id: secondRequestId,
        text,
        input_asset: null,
        task_id: expectedTaskId,
        inspection_mode: "yolo_vlm",
      });
    });
  });

  it("clears a selected frame when the user chooses an attachment or regular task", async () => {
    const fetchMock = vi.fn(async (url: string) => {
      if (url === `/api/sessions/${sessionId}`) {
        return response({
          session: { session_id: sessionId, created_at: createdAt },
          tasks: [videoTask(), regularTask()],
        });
      }
      if (url === `/api/sessions/${sessionId}/turns`) return response([]);
      if (url === `/api/tasks/${videoTaskId}/video`) return response(videoDetail());
      throw new Error(`unexpected ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<App pollIntervalMs={60_000} />);

    await user.click(await screen.findByRole("button", { name: "展开视频候选时间线" }));
    await user.click(await screen.findByText("选择已存证据帧（1）"));
    const selectFrame = await screen.findByRole("button", { name: "选择证据帧 30 用于工具" });
    await user.click(selectFrame);
    expect(screen.getByText(/已选视频证据帧/)).toBeTruthy();
    await user.upload(
      screen.getByLabelText("对话附件（可选）"),
      new File(["image"], "new.jpg", { type: "image/jpeg" }),
    );
    expect(screen.queryByText(/已选视频证据帧/)).toBeNull();

    await user.click(selectFrame);
    await user.selectOptions(screen.getByLabelText("关联已有任务（可选）"), regularTaskId);
    expect(screen.queryByText(/已选视频证据帧/)).toBeNull();
  });

  it("disables frame selection while an existing conversation turn is active", async () => {
    const fetchMock = vi.fn(async (url: string) => {
      if (url === `/api/sessions/${sessionId}`) {
        return response({ session: { session_id: sessionId, created_at: createdAt }, tasks: [videoTask()] });
      }
      if (url === `/api/sessions/${sessionId}/turns`) {
        return response([queuedTurn("dddddddd-dddd-4ddd-8ddd-dddddddddddd", "处理中")]);
      }
      if (url === `/api/tasks/${videoTaskId}/video`) return response(videoDetail());
      throw new Error(`unexpected ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<App pollIntervalMs={60_000} />);

    await user.click(await screen.findByRole("button", { name: "展开视频候选时间线" }));
    await user.click(await screen.findByText("选择已存证据帧（1）"));
    expect((await screen.findByRole("button", { name: "选择证据帧 30 用于工具" }) as HTMLButtonElement).disabled)
      .toBe(true);
  });

  it("disables frame switching while a turn submission is in flight", async () => {
    let releaseTurn: (() => void) | undefined;
    const pendingResponse = new Promise<Response>((resolve) => {
      releaseTurn = () => resolve(response(queuedTurn(
        "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
        "请增强这个低照度帧",
      ), 202));
    });
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === `/api/sessions/${sessionId}`) {
        return response({ session: { session_id: sessionId, created_at: createdAt }, tasks: [videoTask()] });
      }
      if (url === `/api/sessions/${sessionId}/turns` && !init) return response([]);
      if (url === `/api/tasks/${videoTaskId}/video`) return response(videoDetail());
      if (url === `/api/sessions/${sessionId}/turns` && init?.method === "POST") return pendingResponse;
      throw new Error(`unexpected ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<App pollIntervalMs={60_000} />);

    await user.click(await screen.findByRole("button", { name: "展开视频候选时间线" }));
    await user.click(await screen.findByText("选择已存证据帧（1）"));
    const selectFrame = await screen.findByRole("button", { name: "选择证据帧 30 用于工具" });
    await user.click(selectFrame);
    const cancelFrame = screen.getByRole("button", { name: "取消选帧" });
    await user.type(screen.getByLabelText("对话内容"), "请增强这个低照度帧");
    await user.click(screen.getByRole("button", { name: "发送消息" }));

    await waitFor(() => {
      expect((selectFrame as HTMLButtonElement).disabled).toBe(true);
      expect((cancelFrame as HTMLButtonElement).disabled).toBe(true);
    });
    releaseTurn?.();
    await waitFor(() => expect(screen.queryByText("已选视频证据帧：帧 30 · 6 秒")).toBeNull());
  });
});

describe("FRAME child task presentation", () => {
  it("shows its frozen source with no findings and never offers the video endpoint", async () => {
    const fetchMock = vi.fn(async (url: string) => {
      if (url === `/api/sessions/${sessionId}`) {
        return response({ session: { session_id: sessionId, created_at: createdAt }, tasks: [frameChild()] });
      }
      if (url === `/api/sessions/${sessionId}/turns`) return response([]);
      throw new Error(`unexpected ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App pollIntervalMs={60_000} />);

    expect(await screen.findByText("视频证据帧工具")).toBeTruthy();
    expect(screen.getByText("来源：帧 30 · 6 秒；仅分析这一帧，不代表整段视频结论。")).toBeTruthy();
    expect(screen.getByRole<HTMLAnchorElement>("link", { name: "打开来源视频" }).href)
      .toContain(`/api/assets/${videoAsset.asset_id}/versions/1/content`);
    expect(screen.getByRole<HTMLAnchorElement>("link", { name: "打开证据帧" }).href)
      .toContain(`/api/assets/${frameAsset.asset_id}/versions/2/content`);
    expect(screen.getByRole("button", { name: "展开图片增强对照" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "展开几何证据" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "展开视频候选时间线" })).toBeNull();
    await waitFor(() => expect(fetchMock.mock.calls.every(([url]) => !String(url).endsWith("/video"))).toBe(true));
  });
});
