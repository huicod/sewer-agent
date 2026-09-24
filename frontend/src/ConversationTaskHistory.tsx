import { FileImageOutlined } from "@ant-design/icons";
import { Empty } from "antd";
import { useMemo } from "react";

import type { RuntimeStatus, TaskRecord } from "./types";

export type ConversationTaskHistoryProps = {
  tasks: TaskRecord[];
  activeTaskId: string;
  onSelectTask(id: string): void;
};

const STATUS_TEXT: Record<RuntimeStatus, string> = {
  queued: "已排队",
  running: "运行中",
  waiting_review: "待复核",
  completed: "已完成",
  partial: "部分完成",
  failed: "失败",
  cancelled: "已取消",
};

type TaskGroup = {
  assetId: string;
  version: number;
  tasks: TaskRecord[];
};

function groupTasks(tasks: TaskRecord[]): TaskGroup[] {
  const groups = new Map<string, TaskGroup>();
  for (const task of tasks) {
    const { asset_id: assetId, version } = task.input_asset;
    const key = JSON.stringify([assetId, version]);
    const group = groups.get(key);
    if (group) group.tasks.push(task);
    else groups.set(key, { assetId, version, tasks: [task] });
  }
  return Array.from(groups.values());
}

function taskKind(task: TaskRecord): string {
  if (task.frame_source) return "证据帧";
  if (task.parent_task_id) return "工具操作";
  return "原图检查";
}

export function ConversationTaskHistory({
  tasks,
  activeTaskId,
  onSelectTask,
}: ConversationTaskHistoryProps) {
  const groups = useMemo(() => groupTasks(tasks), [tasks]);

  return (
    <section className="conversation-task-history" aria-label="对话任务历史">
      {groups.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无任务记录" />
      ) : groups.map((group, index) => {
        const sourceLabel = `${group.assetId.slice(0, 8)} · v${group.version}`;
        return (
          <details className="task-history-group" key={`${group.assetId}:${group.version}`} open>
            <summary>
              <FileImageOutlined aria-hidden="true" />
              <span>影像 {index + 1} · {group.tasks.length} 项任务</span>
              <code title={`${group.assetId} · v${group.version}`}>{sourceLabel}</code>
            </summary>
            <ul>
              {group.tasks.map((task) => {
                const taskId = task.snapshot.task_id;
                const active = taskId === activeTaskId;
                return (
                  <li key={taskId}>
                    <button className={`task-history-item${active ? " task-history-item--active" : ""}`}
                      type="button" aria-label={`查看任务：${task.instruction}`}
                      aria-pressed={active} title={task.instruction}
                      onClick={() => onSelectTask(taskId)}>
                      <span className="task-history-item__title">{task.instruction}</span>
                      <span className="task-history-item__meta">
                        <span>{taskKind(task)}</span>
                        <span aria-hidden="true">·</span>
                        <span>{STATUS_TEXT[task.snapshot.runtime_status]}</span>
                        <span aria-hidden="true">·</span>
                        <code title={taskId}>{taskId.slice(0, 8)}</code>
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </details>
        );
      })}
    </section>
  );
}
