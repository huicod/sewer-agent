import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { RuntimeStatus, TaskRecord } from "./types";
import { WorkspaceSidebar } from "./WorkspaceSidebar";

function task(
  id: string,
  instruction: string,
  status: RuntimeStatus,
  createdAt = "2026-09-23T09:30:00Z",
): TaskRecord {
  return {
    request_id: `request-${id}`,
    session_id: "session-1",
    input_asset: { asset_id: `asset-${id}`, version: 1 },
    instruction,
    created_at: createdAt,
    snapshot: {
      schema_version: "1",
      task_id: id,
      runtime_status: status,
      inspection_result: status === "completed" ? "normal" : "pending",
      stage: status,
      stage_progress: 0,
      overall_progress: 0,
      processed_frames: 0,
      processed_chunks: 0,
      active_tool: null,
      retry_count: 0,
      waiting_reason: null,
      updated_at: createdAt,
      findings: [],
      coverage: {
        sufficient_coverage: false,
        sufficient_quality: false,
        negative_audit_complete: false,
      },
    },
  };
}

const statusTasks = [
  task("queued-0001", "等待调度的管段", "queued"),
  task("running-0002", "正在检查的管段", "running"),
  task("review-0003", "等待人工复核", "waiting_review"),
  task("complete-0004", "检查已经完成", "completed"),
  task("partial-0005", "部分完成检查", "partial"),
  task("failed-0006", "检查执行失败", "failed"),
];

function renderSidebar(tasks: TaskRecord[] = statusTasks) {
  const onSelectTask = vi.fn();
  const onNewInspection = vi.fn();
  const result = render(
    <WorkspaceSidebar
      tasks={tasks}
      activeTaskId="running-0002"
      onSelectTask={onSelectTask}
      onNewInspection={onNewInspection}
      loading={false}
    />,
  );
  return { ...result, onSelectTask, onNewInspection };
}

describe("WorkspaceSidebar", () => {
  it("filters by grouped server status and displays true group counts", async () => {
    const user = userEvent.setup();
    renderSidebar();

    expect(screen.getByRole("radio", { name: "全部 6" })).toBeTruthy();
    expect(screen.getByRole("radio", { name: "待复核 1" })).toBeTruthy();
    expect(screen.getByRole("radio", { name: "进行中 2" })).toBeTruthy();
    expect(screen.getByRole("radio", { name: "已结束 3" })).toBeTruthy();

    await user.click(screen.getByText("进行中 2"));

    expect(screen.getByRole("button", { name: "查看任务：等待调度的管段" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "查看任务：正在检查的管段" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "查看任务：等待人工复核" })).toBeNull();
    expect(screen.queryByRole("button", { name: "查看任务：检查执行失败" })).toBeNull();
  });

  it("searches case-insensitively across the full task id and instruction", async () => {
    const user = userEvent.setup();
    renderSidebar([
      task("f14e997a-ABCD-4321", "东区主管道裂缝复核", "waiting_review"),
      task("05b5c612-ef00-9876", "西区支管例行巡检", "completed"),
    ]);

    const search = screen.getByLabelText("搜索检查任务");
    await user.type(search, "主管道裂缝");
    expect(screen.getByRole("button", { name: "查看任务：东区主管道裂缝复核" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "查看任务：西区支管例行巡检" })).toBeNull();

    await user.clear(search);
    await user.type(search, "EF00-9876");
    expect(screen.getByRole("button", { name: "查看任务：西区支管例行巡检" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "查看任务：东区主管道裂缝复核" })).toBeNull();
  });

  it("only invokes the requested action and does not mutate task data", async () => {
    const user = userEvent.setup();
    const tasks = statusTasks.map((record) => structuredClone(record));
    const originalTasks = structuredClone(tasks);
    const { onNewInspection, onSelectTask } = renderSidebar(tasks);

    const activeTask = screen.getByRole("button", { name: "查看任务：正在检查的管段" });
    expect(activeTask.classList.contains("workspace-task-item--active")).toBe(true);

    await user.click(screen.getByRole("button", { name: "查看任务：检查已经完成" }));
    expect(onSelectTask).toHaveBeenCalledOnce();
    expect(onSelectTask).toHaveBeenCalledWith("complete-0004");
    expect(onNewInspection).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "新建检查" }));
    expect(onNewInspection).toHaveBeenCalledOnce();
    expect(onSelectTask).toHaveBeenCalledOnce();
    expect(tasks).toEqual(originalTasks);
  });

  it("distinguishes an empty session from a search with no matches", async () => {
    const user = userEvent.setup();
    const { rerender } = renderSidebar([]);

    expect(screen.getByText("暂无检查任务")).toBeTruthy();
    expect(screen.getByText("点击“新建检查”开始第一次巡检")).toBeTruthy();

    rerender(
      <WorkspaceSidebar
        tasks={[task("task-0001", "北区管道检查", "completed")]}
        activeTaskId=""
        onSelectTask={vi.fn()}
        onNewInspection={vi.fn()}
        loading={false}
      />,
    );
    await user.type(screen.getByLabelText("搜索检查任务"), "不存在的任务");

    expect(screen.getByText("未找到匹配任务")).toBeTruthy();
    expect(screen.getByText("请调整搜索词或状态筛选")).toBeTruthy();
    expect(screen.queryByText("暂无检查任务")).toBeNull();
  });
});
