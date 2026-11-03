import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import App from "./test/InspectionWorkbench";

const sessionId = "33333333-3333-4333-8333-333333333333";
const firstId = "44444444-4444-4444-8444-444444444444";
const secondId = "55555555-5555-4555-8555-555555555555";
function task(id: string, instruction: string) {
  return {
    request_id: id, session_id: sessionId,
    input_asset: { asset_id: "11111111-1111-4111-8111-111111111111", version: 1 },
    instruction, created_at: "2026-09-23T10:00:00Z",
    snapshot: {
      schema_version: "1", task_id: id, runtime_status: "queued", inspection_result: "pending",
      stage: "queued", stage_progress: 0, overall_progress: 0, processed_frames: 0,
      processed_chunks: 0, active_tool: null, retry_count: 0, waiting_reason: null,
      updated_at: "2026-09-23T10:00:00Z", findings: [],
      coverage: { sufficient_coverage: false, sufficient_quality: false, negative_audit_complete: false },
    },
  };
}
function restore(tasks: ReturnType<typeof task>[]) {
  localStorage.setItem("pipe-inspector.session-id", sessionId);
  const fetch = vi.fn(async (url: string) => new Response(JSON.stringify(
    url.endsWith("/turns") ? [] : { session: { session_id: sessionId, created_at: "2026-09-23T10:00:00Z" }, tasks },
  ), { headers: { "content-type": "application/json" } }));
  vi.stubGlobal("fetch", fetch);
  return fetch;
}
afterEach(() => { localStorage.clear(); vi.unstubAllGlobals(); });

it("keeps the uploaded file reachable from a turn that never created a task", async () => {
  const input = { asset_id: "11111111-1111-4111-8111-111111111111", version: 2 };
  const createdAt = "2026-09-23T10:00:00Z";
  const turn = { request_id: secondId, session_id: sessionId, created_at: createdAt, updated_at: createdAt,
    text: "检查这段录像", task_id: null, input_asset: input, linked_task_id: null, status: "failed",
    stage: "finished", reply: null, attempts: 1, error_code: "planner_failed" };
  const fetch = restore([]);
  fetch.mockResolvedValueOnce(new Response(JSON.stringify({ session: { session_id: sessionId, created_at: createdAt }, tasks: [] })));
  fetch.mockResolvedValueOnce(new Response(JSON.stringify([turn])));
  render(<App pollIntervalMs={60_000} />);
  expect((await screen.findByRole("link", { name: "打开本轮输入文件" })).getAttribute("href"))
    .toBe(`/api/assets/${input.asset_id}/versions/2/content`);
});

it("shows manually saved inspections in time order in a zero-message session", async () => {
  const earlier = task(firstId, "检查管口");
  const later = { ...task(secondId, "检查管底"), created_at: "2026-09-23T10:30:00Z" };
  const fetch = restore([later, earlier]);
  render(<App pollIntervalMs={60_000} />);
  const feed = within(screen.getByLabelText("对话消息"));
  await feed.findByText("检查管口");
  expect(screen.queryByText("从一份现场影像开始")).toBeNull();
  expect(feed.getAllByRole("region", { name: /^检查记录 · / }).map(el => el.ariaLabel))
    .toEqual(["检查记录 · 检查管口", "检查记录 · 检查管底"]);
  await userEvent.click(feed.getByRole("button", { name: "查看任务与证据 · 44444444" }));
  expect(within(screen.getByRole("region", { name: "检查详情" })).getByRole("heading", { name: "检查管口" })).toBeTruthy();
  expect(screen.getByText("0 条消息 · 2 项任务")).toBeTruthy();
  expect(fetch.mock.calls.every(([url]) => !url.endsWith("/start"))).toBe(true);
});

it("keeps each saved task once when a turn creates a task and another turn refers back", async () => {
  const manual = task(firstId, "检查原片");
  const child = { ...task(secondId, "检查证据帧"), parent_task_id: firstId, created_at: "2026-09-23T10:21:00Z" };
  const createdTurn = { request_id: child.request_id, session_id: sessionId, created_at: "2026-09-23T10:20:00Z",
    updated_at: child.created_at, text: "检查证据帧", task_id: firstId, input_asset: null,
    linked_task_id: secondId, status: "completed", stage: "finished", reply: "已提交证据帧检查", attempts: 1, error_code: null };
  const question = { ...createdTurn, request_id: crypto.randomUUID(), created_at: "2026-09-23T10:23:00Z",
    updated_at: "2026-09-23T10:23:00Z", text: "解释原片记录", reply: "原片检查已保存", linked_task_id: firstId };
  const fetch = restore([child, manual]);
  fetch.mockResolvedValueOnce(new Response(JSON.stringify({
    session: { session_id: sessionId, created_at: manual.created_at }, tasks: [child, manual],
  })));
  fetch.mockResolvedValueOnce(new Response(JSON.stringify([createdTurn, question])));
  render(<App pollIntervalMs={60_000} />);
  const transcript = screen.getByLabelText("对话消息");
  await within(transcript).findByText("原片检查已保存");
  const items = [...transcript.children];
  expect(items).toHaveLength(3);
  expect(within(items[0] as HTMLElement).getByText("检查原片")).toBeTruthy();
  expect(within(items[1] as HTMLElement).getByRole("region", { name: "检查记录 · 检查证据帧" })).toBeTruthy();
  expect(within(items[1] as HTMLElement).getAllByText("检查证据帧")).toHaveLength(1);
  expect(within(items[2] as HTMLElement).queryByRole("region", { name: /^检查记录 · / })).toBeNull();
});

it("restores the most recent linked result as the conversation context after reload", async () => {
  localStorage.setItem("pipe-inspector.session-id", sessionId);
  const tasks = [task(firstId, "最早的检查"), task(secondId, "最近的处理结果")];
  vi.stubGlobal("fetch", vi.fn(async (url: string) => new Response(JSON.stringify(
    url.endsWith("/turns") ? [{
      request_id: crypto.randomUUID(), session_id: sessionId, text: "继续处理当前影像",
      task_id: firstId, input_asset: null, linked_task_id: secondId,
      status: "completed", stage: "finished", reply: "最新结果已保存",
      created_at: tasks[0].created_at, updated_at: tasks[0].created_at, attempts: 1, error_code: null,
    }] : { session: { session_id: sessionId, created_at: tasks[0].created_at }, tasks },
  ), { headers: { "content-type": "application/json" } })));
  render(<App pollIntervalMs={60_000} />);
  await screen.findByText("最新结果已保存");
  expect(screen.getByLabelText<HTMLSelectElement>("关联已有任务（可选）").value).toBe(secondId);
});

it("continues with the new linked tool result inside the same conversation", async () => {
  const source = task(firstId, "原图检查");
  source.snapshot.runtime_status = "completed";
  const fetch = restore([source]);
  const user = userEvent.setup();
  render(<App pollIntervalMs={60_000} />);
  await screen.findByRole("button", { name: "查看任务：原图检查" });
  const child = { ...task(secondId, "原图分割"), parent_task_id: firstId };
  child.snapshot.runtime_status = "completed";
  fetch.mockResolvedValueOnce(new Response(JSON.stringify({
    request_id: crypto.randomUUID(), session_id: sessionId, text: "对原图分割",
    task_id: firstId, input_asset: null, linked_task_id: secondId,
    status: "completed", stage: "finished", reply: "工具结果已保存",
    created_at: source.created_at, updated_at: source.created_at, attempts: 1, error_code: null,
  }), { headers: { "content-type": "application/json" } }));
  fetch.mockResolvedValueOnce(new Response(JSON.stringify(child), { headers: { "content-type": "application/json" } }));
  await user.type(screen.getByLabelText("对话内容"), "对原图分割");
  await user.click(screen.getByRole("button", { name: "发送消息" }));
  await screen.findByText("工具结果已保存");
  await waitFor(() => expect(screen.getByLabelText<HTMLSelectElement>("关联已有任务（可选）").value).toBe(secondId));
});

it("restarts a detector-only original with guided review, preserves the draft and waits before sending", async () => {
  localStorage.setItem("pipe-inspector.session-id", sessionId);
  const source = { ...task(firstId, "纯 YOLO 原图"), inspection_mode: "yolo_only" };
  source.snapshot.runtime_status = "completed";
  const guided = { ...task(secondId, "原图复检"), inspection_mode: "yolo_vlm" };
  let completed = false;
  const json = (value: unknown) => new Response(JSON.stringify(value), { headers: { "content-type": "application/json" } });
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === `/api/sessions/${sessionId}`) return json({ session: { session_id: sessionId, created_at: source.created_at }, tasks: [source] });
    if (url.endsWith("/turns") && !init) return json([]);
    if (url === "/api/tasks") return json(guided);
    if (url === `/api/tasks/${secondId}/start`) return json({ ...guided, snapshot: { ...guided.snapshot, runtime_status: "running" } });
    if (url === `/api/tasks/${secondId}`) return json({ ...guided, snapshot: { ...guided.snapshot, runtime_status: completed ? "completed" : "running" } });
    if (url.endsWith("/turns") && init?.method === "POST") return json({
      ...JSON.parse(String(init.body)), session_id: sessionId, status: "completed", stage: "finished",
      reply: "几何请求已接收（测试夹具）", linked_task_id: secondId,
      created_at: source.created_at, updated_at: source.created_at, attempts: 1, error_code: null,
    });
    throw new Error(`unexpected ${url}`);
  });
  vi.stubGlobal("fetch", fetch);
  const user = userEvent.setup();
  render(<App pollIntervalMs={50} />);
  await screen.findByRole("button", { name: "用原图新建复检" });
  const input = screen.getByLabelText<HTMLTextAreaElement>("对话内容", { exact: true });
  await user.type(input, "对原图进行分割与相对量化");
  await user.click(screen.getByRole("button", { name: "用原图新建复检" }));
  await screen.findByText(/原图复检任务尚未完成/);
  expect(input.value).toBe("对原图进行分割与相对量化");
  expect((screen.getByRole("button", { name: "发送消息" }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByLabelText<HTMLSelectElement>("关联已有任务（可选）").value).toBe(secondId);
  const createPayload = JSON.parse(String(fetch.mock.calls.find(([url]) => url === "/api/tasks")![1]!.body));
  expect(createPayload).toMatchObject({ inspection_mode: "yolo_vlm", input_asset: source.input_asset });
  expect(createPayload.instruction).not.toContain("分割");
  completed = true;
  await waitFor(() => expect((screen.getByRole("button", { name: "发送消息" }) as HTMLButtonElement).disabled).toBe(false));
  await user.click(screen.getByRole("button", { name: "发送消息" }));
  await screen.findByText("几何请求已接收（测试夹具）");
  const submission = fetch.mock.calls.find(([url, init]) => url.endsWith("/turns") && init?.method === "POST")!;
  expect(JSON.parse(String(submission[1]!.body))).toMatchObject({
    task_id: secondId, inspection_mode: "yolo_vlm", input_asset: null, text: "对原图进行分割与相对量化",
  });
});

it("switches inspection detail without silently changing the assistant's selected task", async () => {
  const fetch = restore([task(firstId, "上游管段"), task(secondId, "下游管段")]);
  const user = userEvent.setup();
  render(<App pollIntervalMs={60_000} />);
  const first = await screen.findByRole("button", { name: "查看任务：上游管段" });
  await user.click(first);
  await user.selectOptions(screen.getByLabelText("关联已有任务（可选）"), firstId);
  await user.click(screen.getByRole("button", { name: "查看任务：下游管段" }));
  const detail = screen.getByRole("region", { name: "检查详情" });
  expect(within(detail).getByRole("heading", { name: "下游管段" })).toBeTruthy();
  expect(within(detail).queryByRole("heading", { name: "上游管段" })).toBeNull();
  expect((screen.getByLabelText("关联已有任务（可选）") as HTMLSelectElement).value).toBe(firstId);
  expect(fetch).toHaveBeenCalledTimes(2);
});

it("opens a new inspection form on demand and retains its draft when closed", async () => {
  restore([]);
  const user = userEvent.setup();
  render(<App pollIntervalMs={60_000} />);
  await screen.findByText(sessionId);
  expect(screen.queryByLabelText("现场文件")).toBeNull();
  await user.click(screen.getByRole("button", { name: "新建检查" }));
  const dialog = await screen.findByRole("dialog");
  await user.type(within(dialog).getByLabelText("检查要求"), "检查上游管段");
  await user.click(within(dialog).getByRole("button", { name: /关闭|Close/ }));
  await user.click(screen.getByRole("button", { name: "新建检查" }));
  expect((within(await screen.findByRole("dialog")).getByLabelText("检查要求") as HTMLTextAreaElement).value)
    .toBe("检查上游管段");
});

it("keeps the displayed inspection when the assistant accepts a message about another task", async () => {
  const fetch = restore([task(firstId, "上游管段"), task(secondId, "下游管段")]);
  const user = userEvent.setup();
  render(<App pollIntervalMs={60_000} />);
  await user.click(await screen.findByRole("button", { name: "查看任务：下游管段" }));
  await user.selectOptions(screen.getByLabelText("关联已有任务（可选）"), firstId);
  await user.type(screen.getByLabelText("对话内容", { exact: true }), "说明上游状态");
  fetch.mockResolvedValueOnce(new Response(JSON.stringify({
    request_id: "66666666-6666-4666-8666-666666666666", session_id: sessionId,
    text: "说明上游状态", input_asset: null, task_id: firstId,
    created_at: "2026-09-23T10:00:00Z", updated_at: "2026-09-23T10:00:00Z",
    status: "completed", stage: "finished", reply: "上游任务等待启动检查。",
    linked_task_id: firstId, error_code: null, attempts: 1,
  }), { status: 202, headers: { "content-type": "application/json" } }));
  await user.click(screen.getByRole("button", { name: "发送消息" }));
  await screen.findByText("上游任务等待启动检查。");
  expect(within(screen.getByRole("region", { name: "检查详情" }))
    .getByRole("heading", { name: "下游管段" })).toBeTruthy();
  expect(fetch).toHaveBeenCalledTimes(3);
});

it("automatically associates the displayed task and keeps it after sending", async () => {
  const fetch = restore([task(firstId, "上游管段")]);
  const user = userEvent.setup();
  render(<App pollIntervalMs={60_000} />);
  await screen.findByRole("button", { name: "查看任务：上游管段" });
  expect((screen.getByLabelText("关联已有任务（可选）") as HTMLSelectElement).value).toBe(firstId);
  for (const text of ["解释结果", "再解释原始证据"]) {
    fetch.mockResolvedValueOnce(new Response(JSON.stringify({
      request_id: crypto.randomUUID(), session_id: sessionId, text,
      input_asset: null, task_id: firstId,
      created_at: "2026-09-24T10:00:00Z", updated_at: "2026-09-24T10:00:00Z",
      status: "completed", stage: "finished", reply: `${text}已回答`,
      linked_task_id: firstId, error_code: null, attempts: 1,
    }), { status: 202, headers: { "content-type": "application/json" } }));
    await user.type(screen.getByLabelText("对话内容", { exact: true }), text);
    await user.click(screen.getByRole("button", { name: "发送消息" }));
    await screen.findByText(`${text}已回答`);
    expect((screen.getByLabelText("关联已有任务（可选）") as HTMLSelectElement).value).toBe(firstId);
  }
  expect(fetch).toHaveBeenCalledTimes(4);
  for (const call of fetch.mock.calls.slice(2)) {
    const payload = JSON.parse(String((call as unknown as [string, RequestInit])[1].body));
    expect(payload.task_id).toBe(firstId);
    expect(payload.input_asset).toBeNull();
  }
});

it("pins a draft target while browsing and lets the user explicitly use the displayed task", async () => {
  restore([task(firstId, "上游管段"), task(secondId, "下游管段")]);
  const user = userEvent.setup();
  render(<App pollIntervalMs={60_000} />);
  await screen.findByRole("button", { name: "查看任务：上游管段" });
  await user.type(screen.getByLabelText("对话内容", { exact: true }), "解释这个任务");
  await user.click(screen.getByRole("button", { name: "查看任务：下游管段" }));
  expect((screen.getByLabelText("关联已有任务（可选）") as HTMLSelectElement).value).toBe(firstId);
  await user.click(screen.getByRole("button", { name: "讨论当前查看的任务" }));
  expect((screen.getByLabelText("关联已有任务（可选）") as HTMLSelectElement).value).toBe(secondId);
  expect((screen.getByLabelText("对话内容", { exact: true }) as HTMLTextAreaElement).value).toBe("解释这个任务");
});
