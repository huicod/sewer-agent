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
): SessionSummary {
  return {
    session_id: id,
    title,
    created_at: "2026-09-23T08:00:00Z",
    updated_at: updatedAt,
    task_count: taskCount,
    turn_count: turnCount,
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
});
