import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./test/InspectionWorkbench";

const sessionId = "33333333-3333-4333-8333-333333333333";
const requestId = "22222222-2222-4222-8222-222222222222";
const taskId = "44444444-4444-4444-8444-444444444444";
const assetId = "11111111-1111-4111-8111-111111111111";
const createdAt = "2026-09-09T09:00:00Z";

function response(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), { status, headers: { "content-type": "application/json" } });
}

function turn(status = "queued", linkedTaskId: string | null = null) {
  return {
    request_id: requestId,
    session_id: sessionId,
    text: "请检查附件",
    input_asset: { asset_id: assetId, version: 1 },
    task_id: null,
    created_at: createdAt,
    updated_at: createdAt,
    status,
    stage: status === "completed" ? "finished" : status === "running" ? "executing" : "queued",
    reply: status === "completed" ? "检查已启动。" : null,
    linked_task_id: linkedTaskId,
    error_code: null,
    attempts: status === "queued" ? 0 : 1,
  };
}

function task(runtimeStatus = "running") {
  return {
    request_id: "66666666-6666-4666-8666-666666666666",
    session_id: sessionId,
    input_asset: { asset_id: assetId, version: 1 },
    instruction: "请检查附件",
    created_at: createdAt,
    snapshot: {
      schema_version: "1",
      task_id: taskId,
      runtime_status: runtimeStatus,
      inspection_result: runtimeStatus === "completed" ? "normal" : "pending",
      stage: runtimeStatus === "completed" ? "finished" : "detecting",
      stage_progress: runtimeStatus === "completed" ? 1 : 0.4,
      overall_progress: runtimeStatus === "completed" ? 1 : 0.3,
      processed_frames: 1,
      processed_chunks: 0,
      active_tool: runtimeStatus === "completed" ? null : "yolo-primary",
      retry_count: 0,
      waiting_reason: null,
      updated_at: createdAt,
      findings: [],
      coverage: { sufficient_coverage: true, sufficient_quality: true, negative_audit_complete: true },
    },
  };
}

beforeEach(() => {
  localStorage.setItem("pipe-inspector.session-id", sessionId);
});

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("durable conversation panel", () => {
  it("selects either a new attachment or an existing task, never conflicting asset identities", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({ session: { session_id: sessionId, created_at: createdAt }, tasks: [task("completed")] }))
      .mockResolvedValueOnce(response([]))
      .mockResolvedValueOnce(response({ ref: { asset_id: assetId, version: 1 }, kind: "image", media_type: "image/jpeg", byte_size: 3, width: 32, height: 24, duration_seconds: null, parent: null }, 201))
      .mockResolvedValueOnce(response(turn("queued"), 202));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<App pollIntervalMs={60_000} />);
    await screen.findByText(sessionId);
    const selector = screen.getByLabelText<HTMLSelectElement>(/关联已有任务/);
    const fileInput = screen.getByLabelText<HTMLInputElement>(/对话附件/);
    const file = new File(["img"], "pipe.jpg", { type: "image/jpeg" });
    await user.upload(fileInput, file);
    await user.selectOptions(selector, taskId);
    expect(fileInput.files?.length).toBe(0);
    await user.upload(fileInput, file);
    expect(selector.value).toBe("");
    await user.type(screen.getByLabelText("对话内容"), "检查新图片");
    await user.click(screen.getByRole("button", { name: "发送消息" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(4));
    const posted = JSON.parse(String((fetchMock.mock.calls[3]?.[1] as RequestInit).body));
    expect(posted.task_id).toBeNull();
    expect(posted.input_asset.asset_id).toBe(assetId);
  });

  it("restores the transcript and sends a text-only turn", async () => {
    const restored = { ...turn("completed"), text: "上一条问题", input_asset: null, reply: "上一条回答" };
    const sent = { ...turn("queued"), input_asset: null };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({ session: { session_id: sessionId, created_at: createdAt }, tasks: [] }))
      .mockResolvedValueOnce(response([restored]))
      .mockResolvedValueOnce(response(sent, 202));
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValue(requestId);
    const user = userEvent.setup();

    render(<App pollIntervalMs={60_000} />);
    expect(await screen.findByText("上一条回答")).toBeTruthy();
    await user.type(screen.getByLabelText("对话内容"), "请检查附件");
    await user.click(screen.getByRole("button", { name: "发送消息" }));

    expect(await screen.findByText("请检查附件")).toBeTruthy();
    const post = fetchMock.mock.calls[2]?.[1] as RequestInit;
    expect(JSON.parse(String(post.body))).toEqual({
      request_id: requestId,
      text: "请检查附件",
      input_asset: null,
      task_id: null,
      inspection_mode: "yolo_vlm",
    });
    expect((screen.getByLabelText("对话内容") as HTMLTextAreaElement).disabled).toBe(true);
  });

  it("polls an active turn until terminal and does not keep polling it", async () => {
    let turnPolls = 0;
    const fetchMock = vi.fn(async (url: string) => {
      if (url === `/api/sessions/${sessionId}`) return response({ session: { session_id: sessionId, created_at: createdAt }, tasks: [] });
      if (url === `/api/sessions/${sessionId}/turns`) return response([turn("running")]);
      if (url === `/api/sessions/${sessionId}/turns/${requestId}`) {
        turnPolls += 1;
        return response(turn("completed"));
      }
      throw new Error(`unexpected ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App pollIntervalMs={10} />);
    expect(await screen.findByText("检查已启动。")).toBeTruthy();
    await new Promise((resolve) => setTimeout(resolve, 35));
    expect(turnPolls).toBe(1);
  });

  it("retries an ambiguous failure with the same request and uploaded asset", async () => {
    const uploaded = {
      ref: { asset_id: assetId, version: 1 }, kind: "image", media_type: "image/jpeg",
      byte_size: 3, width: 32, height: 24, duration_seconds: null, parent: null,
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({ session: { session_id: sessionId, created_at: createdAt }, tasks: [] }))
      .mockResolvedValueOnce(response([]))
      .mockResolvedValueOnce(response(uploaded, 201))
      .mockRejectedValueOnce(new TypeError("connection reset"))
      .mockResolvedValueOnce(response(turn("queued"), 202));
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValue(requestId);
    const user = userEvent.setup();

    render(<App pollIntervalMs={60_000} />);
    await screen.findByText(sessionId);
    await user.upload(screen.getByLabelText(/对话附件/), new File(["img"], "pipe.jpg", { type: "image/jpeg" }));
    await user.type(screen.getByLabelText("对话内容"), "请检查附件");
    await user.click(screen.getByRole("button", { name: "发送消息" }));
    expect((await screen.findByRole("alert")).textContent).toContain("相同请求编号");
    await user.click(screen.getByRole("button", { name: "重新发送" }));

    expect(fetchMock.mock.calls.filter(([url]) => url === "/api/assets")).toHaveLength(1);
    const posts = fetchMock.mock.calls.filter(([url]) => url === `/api/sessions/${sessionId}/turns` && url !== undefined)
      .filter(([, init]) => init?.method === "POST");
    expect(posts).toHaveLength(2);
    expect((posts[0]?.[1] as RequestInit).body).toBe((posts[1]?.[1] as RequestInit).body);
  });

  it("hydrates a linked inspection and only offers reports after the task finishes", async () => {
    let taskPolls = 0;
    const fetchMock = vi.fn(async (url: string) => {
      if (url === `/api/sessions/${sessionId}`) return response({ session: { session_id: sessionId, created_at: createdAt }, tasks: [] });
      if (url === `/api/sessions/${sessionId}/turns`) return response([turn("completed", taskId)]);
      if (url === `/api/tasks/${taskId}`) {
        taskPolls += 1;
        return response(task(taskPolls === 1 ? "running" : "completed"));
      }
      throw new Error(`unexpected ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App pollIntervalMs={10} />);
    expect(await screen.findByText("对话已回复；关联检查仍在继续")).toBeTruthy();
    expect(screen.queryByRole("link", { name: "JSON 报告" })).toBeNull();
    expect(await screen.findByText("关联检查已完成")).toBeTruthy();
    expect(screen.getByRole<HTMLAnchorElement>("link", { name: "JSON 报告" }).getAttribute("href"))
      .toBe(`/api/tasks/${taskId}/report?format=json`);
    expect(screen.getByRole<HTMLAnchorElement>("link", { name: "HTML 报告" }).getAttribute("href"))
      .toBe(`/api/tasks/${taskId}/report?format=html`);
  });

  it("does not describe a failed linked inspection as completed", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({
        session: { session_id: sessionId, created_at: createdAt },
        tasks: [task("failed")],
      }))
      .mockResolvedValueOnce(response([turn("completed", taskId)]));
    vi.stubGlobal("fetch", fetchMock);

    render(<App pollIntervalMs={60_000} />);

    expect(await screen.findByText("关联检查已失败")).toBeTruthy();
    expect(screen.queryByText("关联检查已完成")).toBeNull();
  });
});
