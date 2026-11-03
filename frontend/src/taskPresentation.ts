import type { InspectionMode, RuntimeStatus, TaskRecord } from "./types";

export const LABEL_TEXT = {
  AJ: "支管暗接",
  BX: "变形",
  FS: "腐蚀",
  PL: "破裂",
  BLOCK: "堵塞",
  OTHER: "其他类别",
  UNLABELED: "未分类",
} as const;

export const REVIEW_TEXT = {
  confirmed: "已确认",
  tentative: "暂定",
  needs_review: "需复核",
  rejected: "已排除",
} as const;

export const INSPECTION_MODE_TEXT: Record<InspectionMode, string> = {
  yolo_vlm: "YOLO＋VLM 复检",
  yolo_only: "纯 YOLO（不复检）",
};

export const STATUS_TEXT: Record<RuntimeStatus, string> = {
  queued: "等待处理",
  running: "处理中",
  waiting_review: "等待人工复核",
  completed: "已完成",
  partial: "部分完成",
  failed: "处理失败",
  cancelled: "已取消",
};
export const STATUS_COLOR: Record<RuntimeStatus, string> = {
  queued: "default",
  running: "processing",
  waiting_review: "warning",
  completed: "success",
  partial: "warning",
  failed: "error",
  cancelled: "default",
};

const RESULT_TEXT = {
  pending: "待定",
  normal: "正常",
  defects_found: "发现缺陷",
  inconclusive: "无法确定",
} as const;

export function inspectionResultText(task: TaskRecord): string {
  const snapshot = task.snapshot;
  if (task.inspection_mode === "yolo_only" && snapshot.runtime_status === "completed" &&
      snapshot.inspection_result === "inconclusive" && snapshot.processed_frames > 0 &&
      snapshot.coverage.sufficient_coverage && snapshot.findings.length === 0) {
    return "未检出五类缺陷（未复检）";
  }
  return RESULT_TEXT[snapshot.inspection_result];
}
