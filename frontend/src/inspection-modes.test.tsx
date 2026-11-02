import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./test/InspectionWorkbench";
import InspectionConversation, { type ConversationDraft } from "./InspectionConversation";
import { isTaskRecord, isTurnRecord } from "./validators";

const sessionId = "33333333-3333-4333-8333-333333333333";
const taskId = "44444444-4444-4444-8444-444444444444";
const assetId = "11111111-1111-4111-8111-111111111111";
const createdAt = "2026-09-23T09:00:00Z";

const uploadedImage = {
  ref: { asset_id: assetId, version: 1 },
  kind: "image",
  media_type: "image/jpeg",
  byte_size: 3,
  width: 32,
  height: 24,
  duration_seconds: null,
  parent: null,
};

function task(inspectionMode?: "yolo_only" | "yolo_vlm") {
  return {
    request_id: "22222222-2222-4222-8222-222222222222",
    session_id: sessionId,
    input_asset: uploadedImage.ref,
    instruction: "检查破损",
    created_at: createdAt,
    ...(inspectionMode ? { inspection_mode: inspectionMode } : {}),
    snapshot: {
      schema_version: "1",
      task_id: taskId,
      runtime_status: "completed",
      inspection_result: "inconclusive",
      stage: "finished",
      stage_progress: 1,
      overall_progress: 1,
      processed_frames: 1,
      processed_chunks: 0,
      active_tool: null,
      retry_count: 0,
      waiting_reason: null,
      updated_at: createdAt,
      findings: [],
      coverage: {
        sufficient_coverage: true,
        sufficient_quality: true,
        negative_audit_complete: false,
      },
    },
  };
}

function turn(inspectionMode?: "yolo_only" | "yolo_vlm") {
  return {
    request_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    session_id: sessionId,
    text: "检查附件",
    input_asset: uploadedImage.ref,
    task_id: null,
    ...(inspectionMode ? { inspection_mode: inspectionMode } : {}),
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

function response(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function directPanel() {
  return screen.getByRole("heading", { name: "提交现场资料" }).closest("section")!;
}

async function openDirectPanel(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: "新建检查" }));
  await screen.findByRole("heading", { name: "提交现场资料" });
  return directPanel();
}

function conversationPanel() {
  return screen.getByRole("heading", { name: "检查对话" }).closest("section")!;
}

beforeEach(() => {
  localStorage.setItem("pipe-inspector.session-id", sessionId);
});

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("image inspection modes", () => {
  it("reuses a server example asset and the request ID on retry after restoring the session draft", async () => {
    const catalog = [
      { id: "pipe-opening", title: "支管接入", instruction: "检查支管接入口" },
      { id: "wall-trace", title: "管壁线条", instruction: "检查管壁线条" },
    ];
    const video = { ...uploadedImage, kind: "video", media_type: "video/mp4", duration_seconds: 4 };
    const posts: object[] = [];
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/example-videos") return response(catalog);
      if (url === "/api/example-videos/wall-trace/asset") return response(video, 201);
      if (url === "/api/tasks") {
        posts.push(JSON.parse(String(init?.body)));
        return posts.length === 1 ? response({ detail: "请重试" }, 503) : response(task("yolo_vlm"), 201);
      }
      if (url.endsWith("/turns")) return response([]);
      return response({ session: { session_id: sessionId, created_at: createdAt }, tasks: [] });
    });
    vi.stubGlobal("fetch", fetchMock);
    const cache = new Map<string, ConversationDraft>();
    const user = userEvent.setup();
    const component = (key: string) => <InspectionConversation key={key} sessionId={sessionId} draftCache={cache} />;
    const view = render(component("first"));
    await screen.findByText(sessionId);
    let panel = within(await openDirectPanel(user));
    expect(fetchMock.mock.calls.some(([url]) => url.includes("example-videos"))).toBe(false);
    await user.selectOptions(panel.getByLabelText("素材来源"), "example");
    await panel.findByRole("option", { name: "支管接入" });
    await user.selectOptions(panel.getByLabelText("项目示例视频"), "pipe-opening");
    expect(panel.getByLabelText<HTMLTextAreaElement>("检查要求").value).toBe("检查支管接入口");
    await user.clear(panel.getByLabelText("检查要求"));
    await user.type(panel.getByLabelText("检查要求"), "列出证据帧");
    await user.selectOptions(panel.getByLabelText("项目示例视频"), "wall-trace");
    expect(panel.getByLabelText<HTMLTextAreaElement>("检查要求").value).toBe("列出证据帧");
    expect(panel.getByLabelText<HTMLSelectElement>("检查模式").disabled).toBe(true);
    await user.click(panel.getByRole("button", { name: "提交检查" }));
    await panel.findByRole("alert");

    view.rerender(component("restored"));
    await screen.findByText(sessionId);
    panel = within(await openDirectPanel(user));
    await panel.findByRole("option", { name: "管壁线条" });
    expect(panel.getByLabelText<HTMLSelectElement>("项目示例视频").value).toBe("wall-trace");
    expect(panel.getByLabelText<HTMLTextAreaElement>("检查要求").value).toBe("列出证据帧");
    await user.click(panel.getByRole("button", { name: "提交检查" }));
    await waitFor(() => expect(posts).toHaveLength(2));
    expect(posts[0]).toEqual(posts[1]);
    expect(posts[0]).toMatchObject({ input_asset: video.ref, inspection_mode: "yolo_vlm", instruction: "列出证据帧" });
    const assetCalls = fetchMock.mock.calls.filter(([url]) => url.endsWith("/asset"));
    expect(assetCalls).toEqual([["/api/example-videos/wall-trace/asset", { method: "POST" }]]);
    expect(fetchMock.mock.calls.map(([url]) => url)).not.toContain("/api/assets");
  });

  it("allows a failed example catalog to retry and switch back to the pending file upload", async () => {
    let catalogAttempts = 0;
    const fetchMock = vi.fn(async (url: string) => {
      if (url === "/api/example-videos") return ++catalogAttempts === 1
        ? response({ detail: "暂时不可用" }, 503)
        : response([{ id: "clip", title: "管道示例", instruction: "视频提示" }]);
      if (url === "/api/assets") return response(uploadedImage, 201);
      if (url === "/api/tasks") return response(task("yolo_only"), 201);
      if (url.endsWith("/turns")) return response([]);
      return response({ session: { session_id: sessionId, created_at: createdAt }, tasks: [] });
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText(sessionId);
    const panel = within(await openDirectPanel(user));
    const file = new File(["site-photo"], "site.jpg", { type: "image/jpeg" });
    await user.upload(panel.getByLabelText("现场文件"), file);
    await user.selectOptions(panel.getByLabelText("检查模式"), "yolo_only");
    await user.type(panel.getByLabelText("检查要求"), "检查接缝");
    await user.selectOptions(panel.getByLabelText("素材来源"), "example");
    await user.click(await panel.findByRole("button", { name: "重试读取示例" }));
    await panel.findByRole("option", { name: "管道示例" });
    await user.selectOptions(panel.getByLabelText("项目示例视频"), "clip");
    await user.selectOptions(panel.getByLabelText("素材来源"), "file");
    expect(panel.getByLabelText<HTMLInputElement>("现场文件").files?.[0]).toBe(file);
    expect(panel.getByLabelText<HTMLSelectElement>("检查模式").value).toBe("yolo_only");
    expect(panel.getByLabelText<HTMLTextAreaElement>("检查要求").value).toBe("检查接缝");
    await user.click(panel.getByRole("button", { name: "提交检查" }));
    await waitFor(() => expect(fetchMock.mock.calls.map(([url]) => url)).toContain("/api/assets"));
    expect(fetchMock.mock.calls.some(([url]) => url.endsWith("/asset"))).toBe(false);
  });
  it("submits pure YOLO from both new-image entry points", async () => {
    const directTask = task("yolo_only");
    const conversationTurn = turn("yolo_only");
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({
        session: { session_id: sessionId, created_at: createdAt },
        tasks: [],
      }))
      .mockResolvedValueOnce(response([]))
      .mockResolvedValueOnce(response(uploadedImage, 201))
      .mockResolvedValueOnce(response(directTask, 201))
      .mockResolvedValueOnce(response(uploadedImage, 201))
      .mockResolvedValueOnce(response(conversationTurn, 202));
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(globalThis.crypto, "randomUUID")
      .mockReturnValueOnce("22222222-2222-4222-8222-222222222222")
      .mockReturnValueOnce("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa");
    const user = userEvent.setup();

    render(<App pollIntervalMs={60_000} />);
    await screen.findByText(sessionId);

    const direct = within(await openDirectPanel(user));
    await user.upload(
      direct.getByLabelText("现场文件"),
      new File(["image"], "pipe.jpg", { type: "image/jpeg" }),
    );
    expect(direct.getByLabelText<HTMLSelectElement>("检查模式").value).toBe("yolo_vlm");
    await user.selectOptions(direct.getByLabelText("检查模式"), "yolo_only");
    await user.type(direct.getByLabelText("检查要求"), "检查破损");
    await user.click(direct.getByRole("button", { name: "提交检查" }));

    const conversation = within(conversationPanel());
    await user.upload(
      conversation.getByLabelText(/对话附件/),
      new File(["image-2"], "pipe-2.png", { type: "image/png" }),
    );
    await user.selectOptions(conversation.getByLabelText("检查模式"), "yolo_only");
    await user.type(conversation.getByLabelText("对话内容"), "检查附件");
    await user.click(conversation.getByRole("button", { name: "发送消息" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(6));
    expect(JSON.parse(String((fetchMock.mock.calls[3]?.[1] as RequestInit).body)).inspection_mode)
      .toBe("yolo_only");
    expect(JSON.parse(String((fetchMock.mock.calls[5]?.[1] as RequestInit).body)).inspection_mode)
      .toBe("yolo_only");
  });

  it("keeps retry identity immutable, but changing mode creates a new request without re-uploading", async () => {
    const pureRequestId = "22222222-2222-4222-8222-222222222222";
    const reviewedRequestId = "66666666-6666-4666-8666-666666666666";
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({
        session: { session_id: sessionId, created_at: createdAt },
        tasks: [],
      }))
      .mockResolvedValueOnce(response([]))
      .mockResolvedValueOnce(response(uploadedImage, 201))
      .mockRejectedValueOnce(new TypeError("connection reset"))
      .mockRejectedValueOnce(new TypeError("connection reset"))
      .mockResolvedValueOnce(response({
        ...task("yolo_vlm"), request_id: reviewedRequestId,
      }, 201));
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(globalThis.crypto, "randomUUID")
      .mockReturnValueOnce(pureRequestId)
      .mockReturnValueOnce(reviewedRequestId);
    const user = userEvent.setup();

    render(<App pollIntervalMs={60_000} />);
    await screen.findByText(sessionId);
    const panel = within(await openDirectPanel(user));
    await user.upload(
      panel.getByLabelText("现场文件"),
      new File(["image"], "pipe.jpg", { type: "image/jpeg" }),
    );
    await user.selectOptions(panel.getByLabelText("检查模式"), "yolo_only");
    await user.type(panel.getByLabelText("检查要求"), "检查破损");

    await user.click(panel.getByRole("button", { name: "提交检查" }));
    await screen.findByRole("alert");
    await user.click(panel.getByRole("button", { name: "提交检查" }));
    await screen.findByRole("alert");
    await user.selectOptions(panel.getByLabelText("检查模式"), "yolo_vlm");
    await user.click(panel.getByRole("button", { name: "提交检查" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(6));
    expect(fetchMock.mock.calls.filter(([url]) => url === "/api/assets")).toHaveLength(1);
    const posts = fetchMock.mock.calls
      .filter(([url]) => url === "/api/tasks")
      .map(([, init]) => JSON.parse(String((init as RequestInit).body)));
    expect(posts).toHaveLength(3);
    expect(posts[1]).toEqual(posts[0]);
    expect(posts[0]).toMatchObject({ request_id: pureRequestId, inspection_mode: "yolo_only" });
    expect(posts[2]).toMatchObject({ request_id: reviewedRequestId, inspection_mode: "yolo_vlm" });
    expect(posts[2].input_asset).toEqual(posts[0].input_asset);
  });

  it("treats a conversation mode change as new intent while reusing the selected upload", async () => {
    const pureRequestId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
    const reviewedRequestId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
    const reviewedTurn = {
      ...turn("yolo_vlm"),
      request_id: reviewedRequestId,
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({
        session: { session_id: sessionId, created_at: createdAt },
        tasks: [],
      }))
      .mockResolvedValueOnce(response([]))
      .mockResolvedValueOnce(response(uploadedImage, 201))
      .mockRejectedValueOnce(new TypeError("connection reset"))
      .mockResolvedValueOnce(response(reviewedTurn, 202));
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(globalThis.crypto, "randomUUID")
      .mockReturnValueOnce(pureRequestId)
      .mockReturnValueOnce(reviewedRequestId);
    const user = userEvent.setup();

    render(<App pollIntervalMs={60_000} />);
    await screen.findByText(sessionId);
    const panel = within(conversationPanel());
    await user.upload(
      panel.getByLabelText(/对话附件/),
      new File(["image"], "pipe.jpg", { type: "image/jpeg" }),
    );
    await user.selectOptions(panel.getByLabelText("检查模式"), "yolo_only");
    await user.type(panel.getByLabelText("对话内容"), "检查附件");
    await user.click(panel.getByRole("button", { name: "发送消息" }));
    await screen.findByRole("alert");

    await user.selectOptions(panel.getByLabelText("检查模式"), "yolo_vlm");
    await user.click(panel.getByRole("button", { name: "发送消息" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(5));
    expect(fetchMock.mock.calls.filter(([url]) => url === "/api/assets")).toHaveLength(1);
    const posts = fetchMock.mock.calls
      .filter(([url, init]) => url === `/api/sessions/${sessionId}/turns` && init?.method === "POST")
      .map(([, init]) => JSON.parse(String((init as RequestInit).body)));
    expect(posts[0]).toMatchObject({ request_id: pureRequestId, inspection_mode: "yolo_only" });
    expect(posts[1]).toMatchObject({ request_id: reviewedRequestId, inspection_mode: "yolo_vlm" });
    expect(posts[1].input_asset).toEqual(posts[0].input_asset);
  });

  it("preserves the stored mode when confirming a queued conversation turn", async () => {
    const storedTurn = turn("yolo_only");
    const runningTurn = { ...storedTurn, status: "running", stage: "executing", attempts: 1 };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({
        session: { session_id: sessionId, created_at: createdAt },
        tasks: [],
      }))
      .mockResolvedValueOnce(response([storedTurn]))
      .mockResolvedValueOnce(response(runningTurn, 202));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(<App pollIntervalMs={60_000} />);
    await user.click(await screen.findByRole("button", { name: "确认启动" }));

    const posted = JSON.parse(String((fetchMock.mock.calls[2]?.[1] as RequestInit).body));
    expect(posted.inspection_mode).toBe("yolo_only");
    expect(posted.request_id).toBe(storedTurn.request_id);
  });

  it("inherits pure mode when starting a new turn from a saved pure task", async () => {
    const requestId = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
    const savedPureTask = task("yolo_only");
    const linkedTurn = {
      ...turn("yolo_only"),
      request_id: requestId,
      text: "继续检查已存图片",
      input_asset: null,
      task_id: taskId,
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({
        session: { session_id: sessionId, created_at: createdAt },
        tasks: [savedPureTask],
      }))
      .mockResolvedValueOnce(response([]))
      .mockResolvedValueOnce(response(linkedTurn, 202));
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValue(requestId);
    const user = userEvent.setup();

    render(<App pollIntervalMs={60_000} />);
    await screen.findByText(sessionId);
    const panel = within(conversationPanel());
    await user.selectOptions(panel.getByLabelText(/关联已有任务/), taskId);

    const mode = panel.getByLabelText<HTMLSelectElement>("检查模式");
    expect(mode.disabled).toBe(true);
    expect(mode.value).toBe("yolo_only");
    expect(panel.getByText(/关联任务沿用已保存的检查模式/)).toBeTruthy();

    await user.type(panel.getByLabelText("对话内容"), "继续检查已存图片");
    await user.click(panel.getByRole("button", { name: "发送消息" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    const posted = JSON.parse(String((fetchMock.mock.calls[2]?.[1] as RequestInit).body));
    expect(posted).toMatchObject({
      request_id: requestId,
      task_id: taskId,
      inspection_mode: "yolo_only",
    });
  });

  it("resets and disables mode selection for videos in both entry points", async () => {
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(response({
        session: { session_id: sessionId, created_at: createdAt },
        tasks: [],
      }))
      .mockResolvedValueOnce(response([])));
    const user = userEvent.setup();

    render(<App pollIntervalMs={60_000} />);
    await screen.findByText(sessionId);

    for (const [panel, fileLabel] of [
      [within(conversationPanel()), /对话附件/],
      [within(await openDirectPanel(user)), "现场文件"],
    ] as const) {
      await user.upload(panel.getByLabelText(fileLabel), new File(["img"], "pipe.jpg", { type: "image/jpeg" }));
      const selector = panel.getByLabelText<HTMLSelectElement>("检查模式");
      await user.selectOptions(selector, "yolo_only");
      await user.upload(panel.getByLabelText(fileLabel), new File(["video"], "pipe.mp4", { type: "video/mp4" }));
      expect(selector.disabled).toBe(true);
      expect(selector.value).toBe("yolo_vlm");
      expect(panel.getByText(/视频仅使用 YOLO＋VLM 复检/)).toBeTruthy();
    }
  });

  it("displays saved and historical modes without inventing a normal pure-YOLO result", async () => {
    const pure = { ...task("yolo_only"), instruction: "纯 YOLO 历史任务" };
    const historical = {
      ...task(),
      request_id: "99999999-9999-4999-8999-999999999999",
      instruction: "默认模式历史任务",
      snapshot: {
        ...task().snapshot,
        task_id: "88888888-8888-4888-8888-888888888888",
      },
    };
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(response({
        session: { session_id: sessionId, created_at: createdAt },
        tasks: [pure, historical],
      }))
      .mockResolvedValueOnce(response([])));

    const user = userEvent.setup();
    render(<App pollIntervalMs={60_000} />);

    const pureCard = within(
      (await screen.findByRole("heading", { name: "纯 YOLO 历史任务" })).closest("article")!,
    );
    expect(pureCard.getByText("纯 YOLO（不复检）")).toBeTruthy();
    expect(pureCard.getByText("未检出五类缺陷（未复检）")).toBeTruthy();
    expect(pureCard.queryByText("正常")).toBeNull();

    await user.click(screen.getByRole("button", { name: "查看任务：默认模式历史任务" }));
    const historicalCard = within(
      (await screen.findByRole("heading", { name: "默认模式历史任务" })).closest("article")!,
    );
    expect(historicalCard.getByText("YOLO＋VLM 复检")).toBeTruthy();
  });

  it("accepts and displays a backend-shaped pure-YOLO rules-confirmed finding", async () => {
    const findingId = "55555555-5555-4555-8555-555555555555";
    const positive = {
      ...task("yolo_only"),
      snapshot: {
        ...task("yolo_only").snapshot,
        inspection_result: "defects_found",
        findings: [{
          finding_id: findingId,
          task_id: taskId,
          canonical_label: "FS",
          open_label: null,
          review_status: "confirmed",
          source: "detector",
          score: 0.81,
          evidence: [{
            asset: uploadedImage.ref,
            region: { width: 32, height: 24, bbox_xyxy: [2, 3, 20, 18] },
            frame_index: null,
            timestamp_seconds: null,
          }],
          confirmed_by: "detector_only_rules",
        }],
      },
    };
    expect(isTaskRecord(positive)).toBe(true);
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(response({
        session: { session_id: sessionId, created_at: createdAt },
        tasks: [positive],
      }))
      .mockResolvedValueOnce(response([])));

    render(<App pollIntervalMs={60_000} />);

    const card = within(
      (await screen.findByRole("heading", { name: "检查破损" })).closest("article")!,
    );
    expect(card.getByText("发现缺陷")).toBeTruthy();
    expect(card.getByText("纯 YOLO 检测规则确认")).toBeTruthy();
    expect(card.getByText("原始检测器分数 0.81")).toBeTruthy();
  });

  it("does not describe an empty failed pure-YOLO task as having completed detection", async () => {
    const failed = {
      ...task("yolo_only"),
      snapshot: {
        ...task("yolo_only").snapshot,
        runtime_status: "failed",
        stage: "detecting",
        overall_progress: 0.2,
        processed_frames: 0,
        coverage: {
          sufficient_coverage: false,
          sufficient_quality: false,
          negative_audit_complete: false,
        },
      },
    };
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(response({
        session: { session_id: sessionId, created_at: createdAt },
        tasks: [failed],
      }))
      .mockResolvedValueOnce(response([])));

    render(<App pollIntervalMs={60_000} />);

    const card = within(
      (await screen.findByRole("heading", { name: "检查破损" })).closest("article")!,
    );
    expect(card.getByText("服务器报告任务处理失败")).toBeTruthy();
    expect(card.getByText("无法确定")).toBeTruthy();
    expect(card.queryByText("未检出五类缺陷（未复检）")).toBeNull();
  });

  it("accepts valid saved modes, defaults old records, and rejects unknown modes", () => {
    expect(isTaskRecord(task())).toBe(true);
    expect(isTaskRecord(task("yolo_only"))).toBe(true);
    expect(isTaskRecord({ ...task(), inspection_mode: "unknown" })).toBe(false);
    expect(isTurnRecord(turn())).toBe(true);
    expect(isTurnRecord(turn("yolo_vlm"))).toBe(true);
    expect(isTurnRecord({ ...turn(), inspection_mode: "unknown" })).toBe(false);
  });
});
