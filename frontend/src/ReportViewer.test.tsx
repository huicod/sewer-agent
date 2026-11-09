import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { getInspectionReport, getInspectionReportHtml } from "./api";
import { JsonReportView } from "./JsonReportView";
import { ReportViewer } from "./ReportViewer";
import type { SessionReport, TaskReport } from "./types";

const sessionId = "33333333-3333-4333-8333-333333333333";
const otherId = "55555555-5555-4555-8555-555555555555";
const taskId = "44444444-4444-4444-8444-444444444444";
const sessionReport: SessionReport = {
  schema_version: "1", session: { session_id: sessionId, created_at: "2026-11-09T08:00:00Z" },
  generated_at: "2026-11-09T09:00:00Z", is_final: false, turns: [], asset_groups: [],
};
const taskReport: TaskReport = {
  schema_version: "1", task: {
    request_id: "22222222-2222-4222-8222-222222222222", session_id: sessionId,
    input_asset: { asset_id: "11111111-1111-4111-8111-111111111111", version: 1 },
    instruction: "检查管道", created_at: "2026-11-09T08:00:00Z",
    snapshot: { schema_version: "1", task_id: taskId, runtime_status: "completed",
      inspection_result: "inconclusive", stage: "finished", stage_progress: 1, overall_progress: 1,
      processed_frames: 1, processed_chunks: 0, active_tool: null, retry_count: 0, waiting_reason: null,
      updated_at: "2026-11-09T09:00:00Z", findings: [],
      coverage: { sufficient_coverage: false, sufficient_quality: false, negative_audit_complete: false } },
  }, tool_calls: [], enhancement_selection: null, enhancement: null, enhancement_review: null,
  geometry: null, image_evidence: null,
};
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), {
  status, headers: { "content-type": "application/json" },
});
const html = (title = "管道影像检查报告") => new Response(`<!doctype html><html><head><title>${title}</title></head><body>
  <h1>${title}</h1><img src="/api/assets/evidence/versions/1/content" alt="证据">
  <a href="/api/tasks/${taskId}/report?format=html">查看来源</a></body></html>`,
{ headers: { "content-type": "text/html; charset=utf-8" } });

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it("loads reports only when opened and embeds actual HTML without allowing scripts", async () => {
  const fetch = vi.fn(async (url: string) => url.endsWith("format=json") ? json(sessionReport) : html());
  vi.stubGlobal("fetch", fetch);
  const props = { source: { kind: "session" as const, id: sessionId }, initialFormat: "html" as const, onClose() {} };
  const { rerender } = render(<ReportViewer {...props} open={false} />);
  expect(fetch).not.toHaveBeenCalled();
  rerender(<ReportViewer {...props} open />);
  const preview = await screen.findByTitle("HTML 检查报告预览") as HTMLIFrameElement;
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(preview.srcdoc).toContain("管道影像检查报告");
  expect(preview.srcdoc).toContain('http-equiv="Content-Security-Policy"');
  expect(preview.srcdoc).toContain("default-src 'none'");
  expect(preview.srcdoc).toContain(`${window.location.origin}/api/assets/evidence/versions/1/content`);
  expect(preview.srcdoc).toContain('target="_blank"');
  expect(preview.getAttribute("sandbox")).not.toContain("allow-scripts");
  expect(await screen.findByText("非最终快照 · 仍有待处理事项")).toBeTruthy();
});

it("shows the data tree, navigable field table and complete raw JSON from the same snapshot", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => url.endsWith("format=json") ? json(sessionReport) : html()));
  render(<ReportViewer source={{ kind: "session", id: sessionId }} initialFormat="json" open onClose={() => {}} />);
  const tree = await screen.findByRole("navigation", { name: "JSON 数据结构" });
  fireEvent.click(within(tree).getByRole("button", { name: /^会话信息/ }));
  const detail = screen.getByRole("region", { name: "JSON 选中内容" });
  expect(within(detail).getByText(sessionId)).toBeTruthy();
  expect(within(detail).getByText("2026-11-09T08:00:00Z")).toBeTruthy();
  fireEvent.click(screen.getByRole("radio", { name: "原始 JSON" }));
  const raw = screen.getByLabelText("完整 JSON 报告");
  expect(JSON.parse(raw.textContent!)).toEqual(sessionReport);
});

it("ignores both late JSON and HTML responses after changing report source", async () => {
  let resolveJson!: (response: Response) => void;
  let resolveHtml!: (response: Response) => void;
  vi.stubGlobal("fetch", vi.fn((url: string) => {
    if (url.includes(sessionId)) return new Promise<Response>((resolve) => {
      if (url.endsWith("format=json")) resolveJson = resolve; else resolveHtml = resolve;
    });
    return Promise.resolve(url.endsWith("format=json")
      ? json({ ...sessionReport, session: { ...sessionReport.session, session_id: otherId }, is_final: true })
      : html("新的报告"));
  }));
  const { rerender } = render(<ReportViewer source={{ kind: "session", id: sessionId }} initialFormat="html" open onClose={() => {}} />);
  rerender(<ReportViewer source={{ kind: "session", id: otherId }} initialFormat="html" open onClose={() => {}} />);
  const preview = await screen.findByTitle("HTML 检查报告预览") as HTMLIFrameElement;
  expect(preview.srcdoc).toContain("新的报告");
  await act(async () => { resolveJson(json(sessionReport)); resolveHtml(html("旧的报告")); });
  expect(preview.srcdoc).toContain("新的报告");
  expect(preview.srcdoc).not.toContain("旧的报告");
  expect(screen.queryByText("非最终快照 · 仍有待处理事项")).toBeNull();
});

it("keeps valid JSON available when HTML fails and reports clipboard failure", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => url.endsWith("format=json") ? json(sessionReport)
    : json({ detail: { message: "报告暂时无法读取" } }, 503)));
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: vi.fn().mockRejectedValue(new Error("denied")) } });
  render(<ReportViewer source={{ kind: "session", id: sessionId }} initialFormat="html" open onClose={() => {}} />);
  expect(await screen.findByText("报告暂时无法读取")).toBeTruthy();
  fireEvent.click(screen.getByRole("radio", { name: "JSON 数据" }));
  await screen.findByRole("navigation", { name: "JSON 数据结构" });
  fireEvent.click(screen.getByRole("button", { name: /复制 JSON/ }));
  expect(await screen.findByText("无法访问剪贴板，请使用下载，或在原始 JSON 中选择并复制。")).toBeTruthy();
  expect(screen.queryByText("已复制完整 JSON 报告")).toBeNull();
});

it("rejects a wrong source and malformed JSON before rendering it as a report", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(json(sessionReport)).mockResolvedValueOnce(json({ schema_version: "1" })));
  await expect(getInspectionReport({ kind: "session", id: otherId })).rejects.toThrow("服务返回的数据格式无效");
  await expect(getInspectionReport({ kind: "task", id: taskId })).rejects.toThrow("服务返回的数据格式无效");
});

it("validates task terminal status and session asset bindings without dropping report fields", async () => {
  const source = { ...taskReport, task_id: taskId, root_task_id: taskId, role: "primary" };
  const report = { ...sessionReport, asset_groups: [{ input_asset: taskReport.task.input_asset, sources: [source] }] };
  const altered = { ...report, asset_groups: [{ ...report.asset_groups[0], input_asset: { ...taskReport.task.input_asset, version: 2 } }] };
  vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(json(taskReport)).mockResolvedValueOnce(json(report))
    .mockResolvedValueOnce(json(altered)).mockResolvedValueOnce(json({ ...taskReport,
      task: { ...taskReport.task, snapshot: { ...taskReport.task.snapshot, runtime_status: "waiting_review" } } })));
  await expect(getInspectionReport({ kind: "task", id: taskId })).resolves.toEqual(taskReport);
  await expect(getInspectionReport({ kind: "session", id: sessionId })).resolves.toEqual(report);
  await expect(getInspectionReport({ kind: "session", id: sessionId })).rejects.toThrow("服务返回的数据格式无效");
  await expect(getInspectionReport({ kind: "task", id: taskId })).rejects.toThrow("服务返回的数据格式无效");
});

it("reports invalid HTML response and network errors clearly", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(json({ unexpected: "data" })).mockRejectedValueOnce(new Error("offline")));
  await expect(getInspectionReportHtml({ kind: "task", id: taskId })).rejects.toThrow("服务返回的报告格式无效");
  await expect(getInspectionReportHtml({ kind: "task", id: taskId })).rejects.toThrow("无法连接本地服务");
});

it("renders JSON strings as text and exposes nested evidence from array records", () => {
  const unsafe = '<img src="x" onerror="alert(1)">';
  const { container } = render(<JsonReportView data={{ findings: [{ finding_id: "one", open_label: unsafe,
    evidence: [{ asset: { asset_id: "original", version: 1 }, timestamp_seconds: 4.5 }] }] }} />);
  fireEvent.click(screen.getByRole("button", { name: "查看已保存发现" }));
  expect(screen.getByText(unsafe)).toBeTruthy();
  expect(container.querySelector("img")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "查看 [0]" }));
  fireEvent.click(screen.getByRole("button", { name: "查看影像证据" }));
  expect(screen.getByText("4.5")).toBeTruthy();
});

it("distinguishes an absent field from explicit null in heterogeneous JSON rows", () => {
  render(<JsonReportView data={[
    { finding_id: "missing-score" },
    { finding_id: "null-score", score: null },
    { finding_id: "zero-score", score: 0 },
  ]} />);
  const missing = screen.getByRole("row", { name: /missing-score/ });
  const explicitNull = screen.getByRole("row", { name: /null-score/ });
  const zero = screen.getByRole("row", { name: /zero-score/ });
  expect(within(missing).getByRole("cell", { name: "未提供" })).toBeTruthy();
  expect(within(missing).queryByRole("cell", { name: "null" })).toBeNull();
  expect(within(explicitNull).getByRole("cell", { name: "null" })).toBeTruthy();
  expect(within(explicitNull).queryByRole("cell", { name: "未提供" })).toBeNull();
  expect(within(zero).getByRole("cell", { name: "0" })).toBeTruthy();
});

it("refreshes only on request and replaces the prior snapshot", async () => {
  const fetch = vi.fn(async (url: string) => url.endsWith("format=json")
    ? json({ ...sessionReport, is_final: fetch.mock.calls.length > 2 }) : html());
  vi.stubGlobal("fetch", fetch);
  render(<ReportViewer source={{ kind: "session", id: sessionId }} initialFormat="json" open onClose={() => {}} />);
  await screen.findByText("非最终快照 · 仍有待处理事项");
  expect(fetch).toHaveBeenCalledTimes(2);
  fireEvent.click(screen.getByRole("button", { name: /刷新/ }));
  await waitFor(() => expect(screen.queryByText("非最终快照 · 仍有待处理事项")).toBeNull());
  expect(screen.getByText("最终快照", { selector: ".ant-tag" })).toBeTruthy();
  expect(fetch).toHaveBeenCalledTimes(4);
});
