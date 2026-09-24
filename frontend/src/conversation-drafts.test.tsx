import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import InspectionConversation, { type ConversationDraft } from "./InspectionConversation";

const a = "11111111-1111-4111-8111-111111111111";
const b = "22222222-2222-4222-8222-222222222222";
const created = "2026-09-24T10:00:00Z";
const json = (value: unknown) => new Response(JSON.stringify(value), { headers: { "content-type": "application/json" } });
afterEach(() => { localStorage.clear(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it("restores each conversation's text draft without borrowing the other conversation's input", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => url.endsWith("/turns") ? json([]) :
    json({ session: { session_id: url.split("/").at(-1), created_at: created }, tasks: [] })));
  const cache = new Map<string, ConversationDraft>();
  const user = userEvent.setup();
  const view = render(<InspectionConversation key={a} sessionId={a} draftCache={cache} />);
  await screen.findByText(a);
  await user.type(screen.getByLabelText("对话内容"), "图片 A 的草稿");
  view.rerender(<InspectionConversation key={b} sessionId={b} draftCache={cache} />);
  await screen.findByText(b);
  expect(screen.getByLabelText<HTMLTextAreaElement>("对话内容").value).toBe("");
  await user.type(screen.getByLabelText("对话内容"), "图片 B 的草稿");
  view.rerender(<InspectionConversation key={a} sessionId={a} draftCache={cache} />);
  await screen.findByText(a);
  expect(screen.getByLabelText<HTMLTextAreaElement>("对话内容").value).toBe("图片 A 的草稿");
  expect(cache.get(b)?.text).toBe("图片 B 的草稿");
});

it("keeps a late admission in its original conversation and clears only that submitted draft", async () => {
  let resolve!: (response: Response) => void;
  let accepted: Record<string, unknown> | null = null;
  const admission = new Promise<Response>((done) => { resolve = done; });
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      accepted = { ...JSON.parse(String(init.body)), session_id: a,
        created_at: created, updated_at: created, status: "completed", stage: "finished",
        linked_task_id: null, reply: "A 的回复", attempts: 1, error_code: null };
      return admission;
    }
    if (url.endsWith("/turns")) return json(url.includes(a) && accepted ? [accepted] : []);
    return json({ session: { session_id: url.split("/").at(-1), created_at: created }, tasks: [] });
  });
  vi.stubGlobal("fetch", fetch);
  const cache = new Map<string, ConversationDraft>();
  const user = userEvent.setup();
  const view = render(<InspectionConversation key={a} sessionId={a} draftCache={cache} />);
  await screen.findByText(a);
  await user.type(screen.getByLabelText("对话内容"), "A 已提交的问题");
  await user.click(screen.getByRole("button", { name: "发送消息" }));
  await waitFor(() => expect(accepted).not.toBeNull());
  view.rerender(<InspectionConversation key={b} sessionId={b} draftCache={cache} />);
  await screen.findByText(b);
  await user.type(screen.getByLabelText("对话内容"), "B 正在编辑");
  await act(async () => { resolve(json(accepted)); await admission; });
  expect(screen.queryByText("A 的回复")).toBeNull();
  expect(screen.getByLabelText<HTMLTextAreaElement>("对话内容").value).toBe("B 正在编辑");
  view.rerender(<InspectionConversation key={a} sessionId={a} draftCache={cache} />);
  await screen.findByText("A 的回复");
  expect(screen.getByLabelText<HTMLTextAreaElement>("对话内容").value).toBe("");
});

it("keeps R2 retry identity when R1 responds after A is remounted", async () => {
  const r1 = "33333333-3333-4333-8333-333333333333";
  const r2 = "44444444-4444-4444-8444-444444444444";
  const replacement = "55555555-5555-4555-8555-555555555555";
  vi.spyOn(globalThis.crypto, "randomUUID")
    .mockReturnValueOnce(r1).mockReturnValueOnce(r2).mockReturnValueOnce(replacement);

  let resolveR1!: (response: Response) => void;
  let rejectR2!: (error: Error) => void;
  const r1Response = new Promise<Response>((resolve) => { resolveR1 = resolve; });
  const r2Response = new Promise<Response>((_, reject) => { rejectR2 = reject; });
  const posted: Array<Record<string, unknown>> = [];
  let savedR1: Record<string, unknown> | null = null;
  let postCount = 0;
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      const body = JSON.parse(String(init.body)) as Record<string, unknown>;
      posted.push(body);
      postCount += 1;
      if (postCount === 1) {
        savedR1 = { ...body, session_id: a, created_at: created, updated_at: created,
          status: "completed", stage: "finished", linked_task_id: null,
          reply: "R1 已完成", attempts: 1, error_code: null };
        return r1Response;
      }
      if (postCount === 2) return r2Response;
      return json({ ...body, session_id: a, created_at: created, updated_at: created,
        status: "completed", stage: "finished", linked_task_id: null,
        reply: "R2 重试成功", attempts: 2, error_code: null });
    }
    if (url.endsWith("/turns")) return json(url.includes(a) && savedR1 ? [savedR1] : []);
    return json({ session: { session_id: url.split("/").at(-1), created_at: created }, tasks: [] });
  });
  vi.stubGlobal("fetch", fetch);

  const cache = new Map<string, ConversationDraft>();
  const user = userEvent.setup();
  const view = render(<InspectionConversation key={a} sessionId={a} draftCache={cache} />);
  await screen.findByText(a);
  await user.type(screen.getByLabelText("对话内容"), "R1");
  await user.click(screen.getByRole("button", { name: "发送消息" }));
  await waitFor(() => expect(posted).toHaveLength(1));

  view.rerender(<InspectionConversation key={b} sessionId={b} draftCache={cache} />);
  await screen.findByText(b);
  view.rerender(<InspectionConversation key={a} sessionId={a} draftCache={cache} />);
  await screen.findByText("R1 已完成");
  await user.clear(screen.getByLabelText("对话内容"));
  await user.type(screen.getByLabelText("对话内容"), "R2");
  await user.click(screen.getByRole("button", { name: "发送消息" }));
  await waitFor(() => expect(posted).toHaveLength(2));

  await act(async () => { resolveR1(json(savedR1)); await r1Response; });
  await act(async () => { rejectR2(new TypeError("connection reset"));
    await r2Response.catch(() => undefined); });
  await screen.findByText(/再次发送将使用相同请求编号/);
  await user.click(screen.getByRole("button", { name: /重新发送|发送消息/ }));
  await waitFor(() => expect(posted).toHaveLength(3));

  expect(posted[1].request_id).toBe(r2);
  expect(posted[2].request_id).toBe(r2);
});

it("keeps an unsent R2 draft when the shared R1 pending response arrives late", async () => {
  let resolveR1!: (response: Response) => void;
  const r1Response = new Promise<Response>((resolve) => { resolveR1 = resolve; });
  let savedR1: Record<string, unknown> | null = null;
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      const body = JSON.parse(String(init.body)) as Record<string, unknown>;
      savedR1 = { ...body, session_id: a, created_at: created, updated_at: created,
        status: "completed", stage: "finished", linked_task_id: null,
        reply: "R1 已完成", attempts: 1, error_code: null };
      return r1Response;
    }
    if (url.endsWith("/turns")) return json(url.includes(a) && savedR1 ? [savedR1] : []);
    return json({ session: { session_id: url.split("/").at(-1), created_at: created }, tasks: [] });
  });
  vi.stubGlobal("fetch", fetch);

  const cache = new Map<string, ConversationDraft>();
  const user = userEvent.setup();
  const view = render(<InspectionConversation key={a} sessionId={a} draftCache={cache} />);
  await screen.findByText(a);
  await user.type(screen.getByLabelText("对话内容"), "R1 已提交");
  await user.click(screen.getByRole("button", { name: "发送消息" }));
  await waitFor(() => expect(savedR1).not.toBeNull());

  view.rerender(<InspectionConversation key={b} sessionId={b} draftCache={cache} />);
  await screen.findByText(b);
  view.rerender(<InspectionConversation key={a} sessionId={a} draftCache={cache} />);
  await screen.findByText("R1 已完成");
  await user.clear(screen.getByLabelText("对话内容"));
  await user.type(screen.getByLabelText("对话内容"), "R2 尚未发送");
  view.rerender(<InspectionConversation key={b} sessionId={b} draftCache={cache} />);
  await screen.findByText(b);

  await act(async () => { resolveR1(json(savedR1)); await r1Response; });
  expect(cache.get(a)?.text).toBe("R2 尚未发送");

  view.rerender(<InspectionConversation key={a} sessionId={a} draftCache={cache} />);
  await screen.findByText("R1 已完成");
  expect(screen.getByLabelText<HTMLTextAreaElement>("对话内容").value).toBe("R2 尚未发送");
});
