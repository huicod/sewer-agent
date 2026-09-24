import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { AssetRef, RuntimeStatus, TaskRecord } from "./types";
import { ConversationTaskHistory } from "./ConversationTaskHistory";

function task(
  id: string,
  instruction: string,
  inputAsset: AssetRef,
  changes: Partial<TaskRecord> = {},
  status: RuntimeStatus = "completed",
): TaskRecord {
  return {
    request_id: `request-${id}`,
    session_id: "session-1",
    input_asset: inputAsset,
    instruction,
    created_at: "2026-09-24T09:30:00Z",
    snapshot: {
      schema_version: "1",
      task_id: id,
      runtime_status: status,
      inspection_result: "normal",
      stage: "finished",
      stage_progress: 1,
      overall_progress: 1,
      processed_frames: 1,
      processed_chunks: 0,
      active_tool: null,
      retry_count: 0,
      waiting_reason: null,
      updated_at: "2026-09-24T09:30:00Z",
      findings: [],
      coverage: {
        sufficient_coverage: true,
        sufficient_quality: true,
        negative_audit_complete: true,
      },
    },
    ...changes,
  };
}

const assetV1 = { asset_id: "11111111-1111-4111-8111-111111111111", version: 1 };
const assetV2 = { ...assetV1, version: 2 };
const otherAsset = { asset_id: "22222222-2222-4222-8222-222222222222", version: 1 };

describe("ConversationTaskHistory", () => {
  it("groups tasks only when the complete input asset reference matches", () => {
    const tasks = [
      task("root-1", "原图检查", assetV1),
      task("tool-1", "低照度增强", assetV1, { parent_task_id: "root-1" }),
      task("version-2", "同一资产的新版本", assetV2),
      task("other-1", "另一份影像", otherAsset),
    ];
    const { container } = render(
      <ConversationTaskHistory tasks={tasks} activeTaskId="root-1" onSelectTask={vi.fn()} />,
    );

    const groups = Array.from(container.querySelectorAll(".task-history-group"));
    expect(groups).toHaveLength(3);
    expect(within(groups[0] as HTMLElement).getByText("影像 1 · 2 项任务")).toBeTruthy();
    expect(within(groups[1] as HTMLElement).getByText("影像 2 · 1 项任务")).toBeTruthy();
    expect(within(groups[2] as HTMLElement).getByText("影像 3 · 1 项任务")).toBeTruthy();
    expect(groups.every((group) => (group as HTMLDetailsElement).open)).toBe(true);
  });

  it("labels root, tool, and frame tasks without changing their source group", () => {
    const frameSource = {
      video_asset: otherAsset,
      frame_index: 12,
      timestamp_seconds: 2.4,
    };
    render(<ConversationTaskHistory tasks={[
      task("root-1", "原始检查", assetV1),
      task("tool-1", "分割工具", assetV1, { parent_task_id: "root-1" }, "running"),
      task("frame-1", "复检证据帧", assetV1, { parent_task_id: "root-1", frame_source: frameSource }, "waiting_review"),
    ]} activeTaskId="frame-1" onSelectTask={vi.fn()} />);

    expect(screen.getByText("原图检查")).toBeTruthy();
    expect(screen.getByText("工具操作")).toBeTruthy();
    expect(screen.getByText("证据帧")).toBeTruthy();
    expect(screen.getByText("已完成")).toBeTruthy();
    expect(screen.getByRole("button", { name: "查看任务：复检证据帧" }).classList
      .contains("task-history-item--active")).toBe(true);
  });

  it("selects the exact task while preserving a manually folded group", async () => {
    const user = userEvent.setup();
    const onSelectTask = vi.fn();
    const { container, rerender } = render(<ConversationTaskHistory tasks={[
      task("root-1", "完整原图检查要求", assetV1),
      task("other-1", "另一影像检查", otherAsset),
    ]} activeTaskId="root-1" onSelectTask={onSelectTask} />);

    const firstGroup = container.querySelector<HTMLDetailsElement>(".task-history-group")!;
    await user.click(firstGroup.querySelector("summary")!);
    expect(firstGroup.open).toBe(false);
    rerender(<ConversationTaskHistory tasks={[
      task("root-1", "完整原图检查要求", assetV1),
      task("other-1", "另一影像检查", otherAsset),
    ]} activeTaskId="other-1" onSelectTask={onSelectTask} />);
    expect(firstGroup.open).toBe(false);

    await user.click(screen.getByRole("button", { name: "查看任务：另一影像检查" }));
    expect(onSelectTask).toHaveBeenCalledWith("other-1");
    expect(screen.getByRole("button", { name: "查看任务：完整原图检查要求" })
      .getAttribute("title")).toBe("完整原图检查要求");
  });
});
