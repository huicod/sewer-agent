import { AimOutlined, PlusOutlined, SearchOutlined } from "@ant-design/icons";
import { Button, Empty, Input, Radio, Spin } from "antd";
import { useMemo, useState } from "react";

import type { RuntimeStatus, TaskRecord } from "./types";

export type WorkspaceSidebarProps = {
  tasks: TaskRecord[];
  activeTaskId: string;
  onSelectTask: (id: string) => void;
  onNewInspection: () => void;
  loading: boolean;
};

type TaskFilter = "all" | "waiting_review" | "running" | "completed";

const STATUS_TEXT: Record<RuntimeStatus, string> = {
  queued: "已排队",
  running: "运行中",
  waiting_review: "待复核",
  completed: "已完成",
  partial: "部分完成",
  failed: "失败",
  cancelled: "已取消",
};

function matchesFilter(status: RuntimeStatus, filter: TaskFilter): boolean {
  if (filter === "all") return true;
  if (filter === "running") return status === "queued" || status === "running";
  if (filter === "completed") return ["completed", "partial", "failed", "cancelled"].includes(status);
  return status === "waiting_review";
}

function formatTimestamp(value: string): string {
  const match = value.match(/^\d{4}-(\d{2})-(\d{2})T(\d{2}):(\d{2})/);
  if (!match) return value;
  return `${match[1]}-${match[2]} ${match[3]}:${match[4]}`;
}

export function WorkspaceSidebar({
  tasks,
  activeTaskId,
  onSelectTask,
  onNewInspection,
  loading,
}: WorkspaceSidebarProps) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<TaskFilter>("all");

  const counts = useMemo(() => ({
    all: tasks.length,
    waiting_review: tasks.filter(
      (task) => task.snapshot.runtime_status === "waiting_review",
    ).length,
    running: tasks.filter((task) =>
      task.snapshot.runtime_status === "queued" || task.snapshot.runtime_status === "running"
    ).length,
    completed: tasks.filter((task) =>
      matchesFilter(task.snapshot.runtime_status, "completed")
    ).length,
  }), [tasks]);

  const visibleTasks = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase();
    return tasks.filter((task) => {
      if (!matchesFilter(task.snapshot.runtime_status, filter)) return false;
      if (!normalizedQuery) return true;
      return task.snapshot.task_id.toLocaleLowerCase().includes(normalizedQuery)
        || task.instruction.toLocaleLowerCase().includes(normalizedQuery);
    });
  }, [filter, query, tasks]);

  const filters: Array<{ label: string; value: TaskFilter }> = [
    { label: `全部 ${counts.all}`, value: "all" },
    { label: `待复核 ${counts.waiting_review}`, value: "waiting_review" },
    { label: `进行中 ${counts.running}`, value: "running" },
    { label: `已结束 ${counts.completed}`, value: "completed" },
  ];

  const hasActiveFilter = filter !== "all" || query.trim().length > 0;

  return (
    <aside className="workspace-sidebar" aria-label="检查任务导航">
      <div className="workspace-brand">
        <span className="workspace-brand__mark" aria-hidden="true">
          <AimOutlined />
        </span>
        <span>
          <strong className="workspace-brand__name">管道巡检</strong>
          <span className="workspace-brand__caption">影像检查工作台</span>
        </span>
      </div>

      <Button
        className="workspace-sidebar__new"
        type="primary"
        icon={<PlusOutlined />}
        aria-label="新建检查"
        block
        onClick={onNewInspection}
      >
        新建检查
      </Button>

      <Input
        className="workspace-search"
        aria-label="搜索检查任务"
        placeholder="搜索任务名称或编号"
        prefix={<SearchOutlined aria-hidden="true" />}
        value={query}
        allowClear
        onChange={(event) => setQuery(event.target.value)}
      />

      <Radio.Group
        className="workspace-filters"
        aria-label="按任务状态筛选"
        optionType="button"
        buttonStyle="solid"
        options={filters}
        value={filter}
        onChange={(event) => setFilter(event.target.value as TaskFilter)}
      />

      <nav aria-label="检查任务" aria-busy={loading}>
        {loading && tasks.length === 0 ? (
          <div className="workspace-task-list" aria-live="polite">
            <Spin size="small" />
            <span>正在加载任务</span>
          </div>
        ) : visibleTasks.length > 0 ? (
          <ul className="workspace-task-list">
            {visibleTasks.map((task) => {
              const taskId = task.snapshot.task_id;
              const status = task.snapshot.runtime_status;
              const active = taskId === activeTaskId;
              return (
                <li key={taskId}>
                  <button
                    className={`workspace-task-item${active ? " workspace-task-item--active" : ""}`}
                    type="button"
                    aria-label={`查看任务：${task.instruction}`}
                    aria-pressed={active}
                    onClick={() => onSelectTask(taskId)}
                  >
                    <span className="workspace-task-item__title" title={task.instruction}>
                      {task.instruction}
                    </span>
                    <span className="workspace-task-item__meta">
                      <span title={taskId}>{taskId.slice(0, 8)}</span>
                      <span aria-hidden="true">·</span>
                      <span>{formatTimestamp(task.created_at)}</span>
                      <span
                        className={`workspace-status-dot workspace-status-dot--${status}`}
                        aria-hidden="true"
                      />
                      <span>{STATUS_TEXT[status]}</span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        ) : (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={hasActiveFilter ? (
              <span>
                <span>未找到匹配任务</span>
                <small>请调整搜索词或状态筛选</small>
              </span>
            ) : (
              <span>
                <span>暂无检查任务</span>
                <small>点击“新建检查”开始第一次巡检</small>
              </span>
            )}
          />
        )}
      </nav>

      <footer className="workspace-sidebar__footer" aria-live="polite">
        {loading ? "正在更新任务" : `显示 ${visibleTasks.length} / 共 ${tasks.length} 项检查`}
      </footer>
    </aside>
  );
}
