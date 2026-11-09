import { Button, Tag } from "antd";
import { TaskInputPreview } from "./TaskInputPreview";
import { inspectionResultText, INSPECTION_MODE_TEXT, STATUS_COLOR, STATUS_TEXT } from "./taskPresentation";
import type { TaskRecord } from "./types";

const dateFormat = new Intl.DateTimeFormat("zh-CN", {
  month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
});

export function ConversationTaskEntry({ task, onViewTask, turnText, compact = false }: {
  task: TaskRecord;
  onViewTask(id: string): void;
  turnText?: string;
  compact?: boolean;
}) {
  const { snapshot } = task;
  const labels = [...new Set(snapshot.findings.map((finding) =>
    finding.canonical_label || finding.open_label).filter(Boolean))];
  return (
    <section className="feed-task" aria-label={`检查记录 · ${task.instruction}`}>
      <div className="feed-task__header">
        <strong>{task.frame_source ? "证据帧检查记录" : task.parent_task_id ? "关联任务记录" : "检查记录"}</strong>
        <Tag color={STATUS_COLOR[snapshot.runtime_status]}>{STATUS_TEXT[snapshot.runtime_status]}</Tag>
        <span className="feed-task__meta">{INSPECTION_MODE_TEXT[task.inspection_mode ?? "yolo_vlm"]}</span>
        <time className="feed-task__meta" dateTime={task.created_at}>
          {dateFormat.format(new Date(task.created_at))}
        </time>
      </div>
      {task.instruction !== turnText && <div className="feed-task__instruction">
        <span className="feed-task__label">检查要求</span>
        <p>{task.instruction}</p>
      </div>}
      {compact ? <a className="feed-task__source" href={`/api/assets/${encodeURIComponent(task.input_asset.asset_id)}/versions/${task.input_asset.version}/content`} target="_blank" rel="noreferrer">打开输入文件</a> : <TaskInputPreview task={task} />}
      <p className="feed-task__summary">
        <strong>检查结果</strong> {inspectionResultText(task)} · 已保存 {snapshot.findings.length} 条发现{labels.length > 0 && `（${labels.join("、")}）`}
      </p>
      <Button size="small" className="feed-task__details" onClick={() => onViewTask(snapshot.task_id)}>
        查看任务与证据 · {snapshot.task_id.slice(0, 8)}
      </Button>
    </section>
  );
}
