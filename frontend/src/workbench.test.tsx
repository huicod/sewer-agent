import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import App from "./App";

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
