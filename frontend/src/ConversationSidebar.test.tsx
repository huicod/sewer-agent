import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ComponentProps } from "react";
import { describe, expect, it, vi } from "vitest";

import type { SessionSummary } from "./types";
import { ConversationSidebar } from "./ConversationSidebar";

function session(
  id: string,
  title: string,
  taskCount: number,
  turnCount: number,
  updatedAt = "2026-09-24T09:30:00Z",
  changes: Partial<SessionSummary> = {},
): SessionSummary {
  return {
    session_id: id,
    title,
    created_at: "2026-09-23T08:00:00Z",
    updated_at: updatedAt,
    task_count: taskCount,
    turn_count: turnCount,
    ...changes,
  };
}

const sessions = [
  session("11111111-1111-4111-8111-111111111111", "北区主管道复检", 6, 9),
  session("22222222-2222-4222-8222-222222222222", "南区支管巡检", 1, 3),
];

function renderSidebar(changes: Partial<ComponentProps<typeof ConversationSidebar>> = {}) {
  const props = {
    sessions,
    activeSessionId: sessions[0].session_id,
    onSelectSession: vi.fn(),
    onNewConversation: vi.fn(),
    onRefresh: vi.fn(),
    loading: false,
    ...changes,
  };
  return { ...render(<ConversationSidebar {...props} />), props };
}

describe("ConversationSidebar", () => {
  it("renders one navigation row per conversation regardless of its task count", () => {
    renderSidebar();

    const rows = screen.getAllByRole("button", { name: /打开对话：/ });
    expect(rows).toHaveLength(2);
    expect(rows[0].getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByText("9 条消息 · 6 项任务")).toBeTruthy();
    expect(screen.getByText("3 条消息 · 1 项任务")).toBeTruthy();
    expect(screen.getAllByText("09-24 09:30")).toHaveLength(2);
  });

  it("searches title and full id and selects the exact session id", async () => {
    const user = userEvent.setup();
    const { props } = renderSidebar();
    const search = screen.getByLabelText("搜索对话");

    await user.type(search, "南区");
    expect(screen.getByRole("button", { name: "打开对话：南区支管巡检" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "打开对话：北区主管道复检" })).toBeNull();

    await user.clear(search);
    await user.type(search, "1111-4111-8111");
    await user.click(screen.getByRole("button", { name: "打开对话：北区主管道复检" }));
    expect(props.onSelectSession).toHaveBeenCalledWith("11111111-1111-4111-8111-111111111111");
  });

  it("invokes new and refresh actions and disables all actions when requested", async () => {
    const user = userEvent.setup();
    const { props, rerender } = renderSidebar();

    await user.click(screen.getByRole("button", { name: "新建对话" }));
    await user.click(screen.getByRole("button", { name: "刷新对话列表" }));
    expect(props.onNewConversation).toHaveBeenCalledOnce();
    expect(props.onRefresh).toHaveBeenCalledOnce();

    rerender(<ConversationSidebar {...props} disabled loading />);
    expect((screen.getByRole("button", { name: "新建对话" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "刷新对话列表" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "打开对话：北区主管道复检" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByRole("navigation", { name: "对话列表" }).getAttribute("aria-busy")).toBe("true");
  });

  it("distinguishes loading, empty, and unmatched search states", async () => {
    const user = userEvent.setup();
    const { rerender } = renderSidebar({ sessions: [], loading: true });
    expect(screen.getByText("正在加载对话")).toBeTruthy();

    rerender(<ConversationSidebar sessions={[]} activeSessionId="" onSelectSession={vi.fn()}
      onNewConversation={vi.fn()} onRefresh={vi.fn()} loading={false} />);
    expect(screen.getByText("暂无对话")).toBeTruthy();

    rerender(<ConversationSidebar sessions={sessions} activeSessionId="" onSelectSession={vi.fn()}
      onNewConversation={vi.fn()} onRefresh={vi.fn()} loading={false} />);
    await user.type(screen.getByLabelText("搜索对话"), "不存在");
    expect(screen.getByText("未找到匹配对话")).toBeTruthy();
  });

  it("opens row actions without selecting and switches between active and archived lists", async () => {
    const user = userEvent.setup();
    const archived = session("33333333-3333-4333-8333-333333333333", "历史对话", 2, 4,
      "2026-09-22T08:00:00Z", { archived: true });
    const onUpdateSession = vi.fn().mockResolvedValue(undefined);
    const { props, rerender } = renderSidebar({ sessions: [...sessions, archived], onUpdateSession });

    await user.click(screen.getByRole("button", { name: "对话操作：北区主管道复检" }));
    expect(props.onSelectSession).not.toHaveBeenCalled();
    await user.click(await screen.findByRole("menuitem", { name: "归档" }));
    expect(onUpdateSession).toHaveBeenCalledWith(sessions[0].session_id, { archived: true });
    expect(props.onSelectSession).not.toHaveBeenCalled();

    rerender(<ConversationSidebar {...props} sessions={[...sessions, archived]} />);
    await user.click(screen.getByText("已归档 1"));
    expect(screen.getByRole("button", { name: "打开对话：历史对话" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "打开对话：北区主管道复检" })).toBeNull();
    onUpdateSession.mockClear();
    await user.click(screen.getByRole("button", { name: "对话操作：历史对话" }));
    await user.click(await screen.findByRole("menuitem", { name: "恢复" }));
    expect(onUpdateSession).toHaveBeenCalledWith(archived.session_id, { archived: false });
  });

  it("renames the exact session with a trimmed title", async () => {
    const user = userEvent.setup();
    const onUpdateSession = vi.fn().mockResolvedValue(undefined);
    renderSidebar({ onUpdateSession });

    await user.click(screen.getByRole("button", { name: "对话操作：南区支管巡检" }));
    await user.click(await screen.findByRole("menuitem", { name: "重命名" }));
    const input = screen.getByLabelText("对话名称");
    await user.clear(input);
    await user.type(input, "  南区复核记录  ");
    await user.click(screen.getByRole("button", { name: "保存" }));

    expect(onUpdateSession).toHaveBeenCalledWith(sessions[1].session_id, { title: "南区复核记录" });
  });

  it("keeps the rename modal open and shows an error when update fails", async () => {
    const user = userEvent.setup();
    const onUpdateSession = vi.fn().mockRejectedValue(new Error("名称保存失败"));
    renderSidebar({ onUpdateSession });

    await user.click(screen.getByRole("button", { name: "对话操作：北区主管道复检" }));
    await user.click(await screen.findByRole("menuitem", { name: "重命名" }));
    await user.click(screen.getByRole("button", { name: "保存" }));

    expect((await screen.findByRole("alert")).textContent).toContain("名称保存失败");
    expect(screen.getByRole("dialog", { name: "重命名对话" })).toBeTruthy();
  });

  it("sorts pinned conversations first and offers the inverse pin action", async () => {
    const user = userEvent.setup();
    const pinned = session("33333333-3333-4333-8333-333333333333", "较早置顶", 1, 1,
      "2026-09-20T08:00:00Z", { pinned: true });
    const onUpdateSession = vi.fn().mockResolvedValue(undefined);
    renderSidebar({ sessions: [...sessions, pinned], onUpdateSession });

    expect(screen.getAllByRole("button", { name: /打开对话：/ })[0]
      .getAttribute("aria-label")).toBe("打开对话：较早置顶");
    expect(screen.getByText("已置顶")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "对话操作：较早置顶" }));
    await user.click(await screen.findByRole("menuitem", { name: "取消置顶" }));
    expect(onUpdateSession).toHaveBeenCalledWith(pinned.session_id, { pinned: false });
  });

  it("returns to the unfiltered active list when creating from archived conversations", async () => {
    const user = userEvent.setup();
    const archived = session("33333333-3333-4333-8333-333333333333", "历史对话", 1, 1,
      "2026-09-20T08:00:00Z", { archived: true });
    const { props } = renderSidebar({ sessions: [...sessions, archived] });

    await user.click(screen.getByText("已归档 1"));
    await user.type(screen.getByLabelText("搜索对话"), "历史");
    await user.click(screen.getByRole("button", { name: "新建对话" }));

    expect(props.onNewConversation).toHaveBeenCalledOnce();
    expect((screen.getByLabelText("搜索对话") as HTMLInputElement).value).toBe("");
    expect(screen.getByRole("button", { name: "打开对话：北区主管道复检" })).toBeTruthy();
  });

  it("blocks every metadata menu while one session is updating", () => {
    renderSidebar({ onUpdateSession: vi.fn(), updatingSessionId: sessions[0].session_id });

    expect((screen.getByRole("button", { name: "对话操作：北区主管道复检" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "对话操作：南区支管巡检" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "打开对话：南区支管巡检" }) as HTMLButtonElement).disabled).toBe(false);
  });
});
