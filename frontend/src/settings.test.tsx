import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import App from "./App";
import { SettingsPanel } from "./SettingsPanel";
import type { ApplicationSettings } from "./types";

const saved: ApplicationSettings = {
  revision: 3,
  llm: { base_url: "https://provider.example/v1", model: "chat-model", key_configured: true, configured: true },
  vlm: { base_url: "https://vision.example/v1", model: "vision-model", key_configured: true, configured: true },
  api_timeout_seconds: 60, api_max_output_tokens: 4096, api_enable_thinking: false,
};
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), {
  status, headers: { "content-type": "application/json" },
});
afterEach(() => { localStorage.clear(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it("sends shared credentials only after an explicit choice, clears typed secrets on success", async () => {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    return json(init?.method === "PUT" ? { ...saved, revision: 4, vlm: saved.llm } : saved);
  }));
  render(<SettingsPanel onClose={() => {}} />);
  const input = await screen.findByLabelText("API Key · 对话助手");
  expect((input as HTMLInputElement).value).toBe("");
  const user = userEvent.setup();
  await user.type(input, "new-private-key");
  await user.click(screen.getByRole("checkbox", { name: "视觉复检使用相同的 API 配置" }));
  await user.click(screen.getByRole("button", { name: "保存设置" }));
  await screen.findByText(/已保存。新的检查/);
  const request = calls.find((call) => call.init?.method === "PUT")!;
  expect(request.url).toBe("/api/settings");
  expect(request.init?.headers).toEqual({ "content-type": "application/json", "X-Sewer-Settings": "1" });
  expect(JSON.parse(String(request.init?.body))).toMatchObject({ revision: 3,
    llm: { api_key: "new-private-key" },
    vlm: { base_url: saved.llm.base_url, model: saved.llm.model, reuse_llm_key: true } });
  expect((screen.getByLabelText("API Key · 对话助手") as HTMLInputElement).value).toBe("");
  expect(JSON.stringify(localStorage)).not.toContain("new-private-key");
});

it("preserves an unsaved edit after a revision conflict and does not save browser preferences", async () => {
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => init?.method === "PUT"
    ? json({ detail: { message: "设置已被其他页面修改，请重新读取。" } }, 409) : json(saved)));
  render(<SettingsPanel onClose={() => {}} />);
  const input = await screen.findByLabelText("模型名称 · 对话助手");
  fireEvent.change(input, { target: { value: "new-model" } });
  await userEvent.click(screen.getByRole("button", { name: "保存设置" }));
  await screen.findByText("设置已被其他页面修改，请重新读取。");
  expect((input as HTMLInputElement).value).toBe("new-model");
  expect(localStorage.getItem("pipe-inspector.default-image-mode")).toBeNull();
});

it("tests the current draft without saving, and invalidates its result when the endpoint changes", async () => {
  const requests: RequestInit[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (init) requests.push(init);
    return json(url.endsWith("/test") ? { ok: true, model_found: true, message: "模型列表连接成功" } : saved);
  }));
  render(<SettingsPanel onClose={() => {}} />);
  fireEvent.change(await screen.findByLabelText("API Key · 对话助手"), { target: { value: "draft-key" } });
  await userEvent.click(screen.getByRole("button", { name: "测试对话助手连接" }));
  await screen.findByText("模型列表连接成功");
  expect(requests).toHaveLength(1);
  expect(JSON.parse(String(requests[0].body))).toEqual({ role: "llm", base_url: saved.llm.base_url,
    model: saved.llm.model, api_key: "draft-key" });
  fireEvent.change(screen.getByLabelText("服务地址 · 对话助手"), { target: { value: "https://changed.example/v1" } });
  expect(screen.queryByText("模型列表连接成功")).toBeNull();
});

it("retries a failed settings read instead of offering a blank form that could overwrite the server", async () => {
  const fetch = vi.fn().mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce(json(saved));
  vi.stubGlobal("fetch", fetch);
  render(<SettingsPanel onClose={() => {}} />);
  await screen.findByText("无法连接本地服务");
  expect((screen.getByRole("button", { name: "保存设置" }) as HTMLButtonElement).disabled).toBe(true);
  await userEvent.click(screen.getByRole("button", { name: "重新读取" }));
  await screen.findByLabelText("服务地址 · 对话助手");
});

it("keeps the conversation draft while settings are open and applies the preference to a new image", async () => {
  const id = "33333333-3333-4333-8333-333333333333";
  const session = { session_id: id, created_at: "2026-11-04T10:00:00Z" };
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/sessions") return json([{ ...session, title: "保留草稿", updated_at: session.created_at,
      task_count: 0, turn_count: 0 }]);
    if (url === `/api/sessions/${id}`) return json({ session, tasks: [] });
    if (url.endsWith("/turns") || url === "/api/example-videos") return json([]);
    if (url === "/api/settings") return json(init?.method === "PUT" ? { ...saved, revision: 4 } : saved);
    throw new Error(`unexpected ${url}`);
  }));
  render(<App pollIntervalMs={60_000} />);
  const draft = await screen.findByRole("textbox", { name: "对话内容" });
  fireEvent.change(draft, { target: { value: "尚未发送的检查要求" } });
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "设置" }));
  await screen.findByLabelText("服务地址 · 对话助手");
  await user.click(screen.getByText("基础设置", { exact: true }));
  await user.selectOptions(screen.getByLabelText("新图片默认检查方式"), "yolo_only");
  await user.click(screen.getByRole("button", { name: "保存设置" }));
  await screen.findByText(/已保存。新的检查/);
  await user.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByText("配置你的检查工作台")).toBeNull());
  expect((draft as HTMLTextAreaElement).value).toBe("尚未发送的检查要求");
  await user.click(screen.getByRole("button", { name: "新建检查" }));
  const dialog = screen.getByRole("dialog");
  await user.upload(within(dialog).getByLabelText("现场文件"), new File(["image"], "sample.jpg", { type: "image/jpeg" }));
  expect((within(dialog).getByLabelText("检查模式") as HTMLSelectElement).value).toBe("yolo_only");
});
