import { useRef, useState } from "react";

import { ApiError, createTask, startTask } from "./api";
import type { TaskRecord, TaskSubmission } from "./types";

const BASELINE_INSTRUCTION = "使用同一原图执行五类缺陷检测和 VLM 基线复检。";

type PendingRestart = {
  sourceTaskId: string;
  submission: TaskSubmission;
  admitted: TaskRecord | null;
};

type GuidedRestartActionProps = {
  compact?: boolean;
  task: TaskRecord | null;
  disabled: boolean;
  onAdmitted(task: TaskRecord): void;
  onStarted(task: TaskRecord): void;
  onBusyChange(busy: boolean): void;
};

function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "无法新建复检任务，请重试";
}

export function GuidedRestartAction({
  compact = false,
  task,
  disabled,
  onAdmitted,
  onStarted,
  onBusyChange,
}: GuidedRestartActionProps) {
  const pending = useRef<PendingRestart | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const eligible = task !== null && (task.inspection_mode ?? "yolo_vlm") === "yolo_only"
    && !task.frame_source && !task.parent_task_id;
  if (!eligible) return null;

  async function restart() {
    if (!task || busy || disabled) return;
    const sourceTaskId = task.snapshot.task_id;
    let current = pending.current;
    if (current?.sourceTaskId !== sourceTaskId) {
      current = {
        sourceTaskId,
        submission: {
          request_id: crypto.randomUUID(),
          session_id: task.session_id,
          input_asset: task.input_asset,
          instruction: BASELINE_INSTRUCTION,
          inspection_mode: "yolo_vlm",
        },
        admitted: null,
      };
      pending.current = current;
    }

    setBusy(true);
    setError(null);
    onBusyChange(true);
    try {
      if (current.admitted === null) {
        current.admitted = await createTask(current.submission);
        onAdmitted(current.admitted);
      }
      const started = await startTask(current.admitted.snapshot.task_id);
      onStarted(started);
      pending.current = null;
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setBusy(false);
      onBusyChange(false);
    }
  }

  return (
    <div className={`guided-restart-action${compact ? " guided-restart-action--compact" : ""}`}>
      <p title="复用原图新建 YOLO＋VLM 检查，完成后可继续使用工具，草稿会保留。">{compact ? "纯 YOLO · 扩展工具需复检" : "当前为纯 YOLO，可追问和导出已存结果。追加复检、增强或几何工具需先使用同一原图新建 YOLO＋VLM 任务；完成后可继续发送当前草稿。"}</p>
      {error ? <div role="alert">{error}</div> : null}
      <button type="button" disabled={disabled || busy} onClick={() => void restart()}>
        {error ? "重试新建复检" : busy ? "正在新建复检…" : "用原图新建复检"}
      </button>
    </div>
  );
}
