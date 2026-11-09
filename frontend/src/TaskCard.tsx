import { EyeOutlined, FileTextOutlined, PlayCircleOutlined, StopOutlined } from "@ant-design/icons";
import { Button, Tag } from "antd";
import { useEffect, useState } from "react";

import { ApiError, getAsset } from "./api";
import { EnhancementPanel } from "./EnhancementPanel";
import { GeometryPanel } from "./GeometryPanel";
import { ImageEvidencePanel } from "./ImageEvidencePanel";
import { ReviewPanel } from "./ReviewPanel";
import { EvidenceThumbnail } from "./EvidenceThumbnail";
import { TaskInputPreview } from "./TaskInputPreview";
import { inspectionResultText, INSPECTION_MODE_TEXT, REVIEW_TEXT, STATUS_COLOR, STATUS_TEXT } from "./taskPresentation";
import type { AssetRef, InspectionMode, RuntimeStatus, TaskRecord, VideoEvidenceFrame } from "./types";
import { VideoTimeline } from "./VideoTimeline";
import "./task-card.css";

const ACTIVE_STATUSES = new Set<RuntimeStatus>(["queued", "running", "waiting_review"]);
const DEFAULT_INSPECTION_MODE: InspectionMode = "yolo_vlm";

const CONFIRMATION_TEXT = {
  rules: "规则确认",
  detector_only_rules: "纯 YOLO 检测规则确认",
  human: "人工确认",
} as const;
const TASK_STAGE_TEXT: Record<string, string> = {
  tools: "图片增强处理",
};
const ACTIVE_TOOL_TEXT: Record<string, string> = {
  video_scene: "视频场景筛选",
  enhancement_selection: "按需选择图片增强",
  low_light: "低照度恢复",
  dehaze: "去雾增强",
  deblur: "去模糊增强",
  super_resolution: "超分辨率增强",
  enhancement_review: "增强辅助复核",
  segment: "提示式掩膜",
  relative_depth: "单图相对深度",
  relative_measure: "掩膜与相对深度量化",
};
const GEOMETRY_TOOLS = new Set(["segment", "relative_depth", "relative_measure"]);

export type TaskCardProps = {
  task: TaskRecord;
  starting: boolean;
  cancelling: boolean;
  onStart: (taskId: string) => void;
  onCancel: (taskId: string) => void;
  onReviewed: (task: TaskRecord, snapshot: TaskRecord["snapshot"]) => void;
  onSelectFrame: (taskId: string, frame: VideoEvidenceFrame) => void;
  frameSelectionDisabled: boolean;
  presentation?: "detail" | "workbench";
  onReport?: (taskId: string, format: "html" | "json") => void;
};

function formatError(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return "发生未知错误，请稍后重试";
}

function evidenceUrl(asset: AssetRef): string {
  return `/api/assets/${encodeURIComponent(asset.asset_id)}/versions/${asset.version}/content`;
}

function reportUrl(taskId: string, format: "json" | "html"): string {
  return `/api/tasks/${encodeURIComponent(taskId)}/report?format=${format}`;
}

function isTerminalTask(task: TaskRecord): boolean {
  return !ACTIVE_STATUSES.has(task.snapshot.runtime_status);
}

function inspectionModeOf(record: { inspection_mode?: InspectionMode }): InspectionMode {
  return record.inspection_mode ?? DEFAULT_INSPECTION_MODE;
}

function taskStageText(task: TaskRecord): string {
  if (
    task.snapshot.stage === "tools" &&
    task.snapshot.active_tool &&
    GEOMETRY_TOOLS.has(task.snapshot.active_tool)
  ) {
    return "几何工具处理";
  }
  return TASK_STAGE_TEXT[task.snapshot.stage] ?? task.snapshot.stage;
}

export function TaskCard({
  task,
  starting,
  cancelling,
  onStart,
  onCancel,
  onReviewed,
  onSelectFrame,
  frameSelectionDisabled,
  presentation = "detail",
  onReport,
}: TaskCardProps) {
  const [reviewOpen, setReviewOpen] = useState(false);
  const [mediaKind, setMediaKind] = useState<string | null>(null);
  const [mediaLoading, setMediaLoading] = useState(false);
  const [mediaError, setMediaError] = useState<string | null>(null);

  useEffect(() => {
    setReviewOpen(false);
    setMediaKind(null);
    setMediaLoading(false);
    setMediaError(null);
  }, [task.snapshot.task_id]);

  const progress = Math.round(
    Math.min(1, Math.max(0, task.snapshot.overall_progress)) * 100,
  );
  const queued = task.snapshot.runtime_status === "queued";
  const waitingReview = task.snapshot.runtime_status === "waiting_review";
  const terminal = isTerminalTask(task);
  const hasVideoLocator = task.snapshot.findings.some((finding) =>
    finding.evidence.some((evidence) => evidence.frame_index !== null),
  );
  const inspectionMode = inspectionModeOf(task);

  async function discoverMedia() {
    if (mediaLoading || mediaKind) return;
    setMediaLoading(true);
    setMediaError(null);
    try {
      const asset = await getAsset(task.input_asset);
      setMediaKind(asset.kind);
    } catch (nextError) {
      setMediaError(formatError(nextError));
    } finally {
      setMediaLoading(false);
    }
  }

  const sourceUrl = evidenceUrl(task.input_asset);

  return (
    <article className={`task-card${presentation === "workbench" ? " task-card--workbench" : ""}`}>
      <header className="task-card__heading task-detail-header">
        <div className="task-detail-title">
          <p className="eyebrow">
            {task.frame_source ? "视频证据帧工具" : task.parent_task_id ? "关联追加复检" : "检查任务"}
          </p>
          <h3>{task.instruction}</h3>
        </div>
        <Tag
          className={`task-detail-status status status--${task.snapshot.runtime_status}`}
          color={STATUS_COLOR[task.snapshot.runtime_status]}
        >
          {STATUS_TEXT[task.snapshot.runtime_status]}
        </Tag>
      </header>

      <div className="task-detail-toolbar" aria-label="任务操作">
        {queued ? (
          <Button
            type="primary"
            icon={<PlayCircleOutlined aria-hidden="true" />}
            disabled={starting}
            loading={starting}
            onClick={() => onStart(task.snapshot.task_id)}
          >
            {starting ? "正在启动…" : task.parent_task_id ? "启动关联复检" : "启动检查"}
          </Button>
        ) : null}
        {waitingReview ? (
          <Button type="primary" onClick={() => setReviewOpen((open) => !open)}>
            {reviewOpen ? "收起人工复核" : "开始人工复核"}
          </Button>
        ) : null}
        {task.snapshot.can_cancel === true && !terminal ? (
          <Button
            danger
            icon={<StopOutlined aria-hidden="true" />}
            disabled={cancelling}
            loading={cancelling}
            onClick={() => onCancel(task.snapshot.task_id)}
          >
            {cancelling ? "正在取消…" : "取消检查"}
          </Button>
        ) : null}
        {!waitingReview && terminal ? (
          <Button icon={<EyeOutlined aria-hidden="true" />} onClick={() => setReviewOpen((open) => !open)}>
            {reviewOpen ? "收起审核记录" : "查看审核记录"}
          </Button>
        ) : null}
        {terminal ? (
          <div className="report-links" aria-label="检查报告">
            <Button
              type="link"
              icon={<FileTextOutlined aria-hidden="true" />}
              href={onReport ? undefined : reportUrl(task.snapshot.task_id, "json")}
              onClick={onReport ? () => onReport(task.snapshot.task_id, "json") : undefined}
              target="_blank"
              rel="noreferrer"
            >
              JSON 报告
            </Button>
            <Button
              type="link"
              icon={<FileTextOutlined aria-hidden="true" />}
              href={onReport ? undefined : reportUrl(task.snapshot.task_id, "html")}
              onClick={onReport ? () => onReport(task.snapshot.task_id, "html") : undefined}
              target="_blank"
              rel="noreferrer"
            >
              HTML 报告
            </Button>
          </div>
        ) : null}
      </div>

      {presentation === "workbench" ? <>
        <TaskInputPreview task={task} variant="stage" onMediaKind={setMediaKind} />
        {task.frame_source ? <div className="task-hint"><p>来源：帧 {task.frame_source.frame_index} · {task.frame_source.timestamp_seconds} 秒；仅分析这一帧，不代表整段视频结论。</p><a href={evidenceUrl(task.frame_source.video_asset)} target="_blank" rel="noreferrer">打开来源视频</a></div> : null}
        {!task.frame_source && (hasVideoLocator || mediaKind === "video") ? <VideoTimeline
          taskId={task.snapshot.task_id} snapshotRevision={task.snapshot.updated_at}
          onSelectFrame={(frame) => onSelectFrame(task.snapshot.task_id, frame)}
          frameSelectionDisabled={frameSelectionDisabled} /> : null}
      </> : null}

      {task.parent_task_id ? (
        <p className="task-hint">
          {task.frame_source
            ? "仅分析已存视频证据帧，不重新扫描视频，也不改写整片结论。"
            : "复用原图片和检测证据，原结果保持不变。"}{" "}
          <a href={reportUrl(task.parent_task_id, "html")} target="_blank" rel="noreferrer">
            原任务报告
          </a>
        </p>
      ) : null}
      {inspectionMode === "yolo_only" ? (
        <p className="truth-note">纯 YOLO 模式未执行 VLM 复检；此处仅显示检测器保存结果。</p>
      ) : null}
      {queued ? (
        <p className="truth-note">
          {task.parent_task_id
            ? "复检已入队，等待执行；重复启动沿用同一任务"
            : "已上传并入队，等待启动检查"}
        </p>
      ) : null}
      {task.snapshot.runtime_status === "running" ? (
        <p className="truth-note">
          {task.parent_task_id ? "关联复检正在执行（不重新检测）" : "检查正在执行"}
        </p>
      ) : null}
      {task.snapshot.runtime_status === "partial" ? (
        <p className="truth-note">检查部分完成；新任务可查看审核记录，旧任务可能仅保留历史结果。</p>
      ) : null}
      {task.snapshot.runtime_status === "failed" ? (
        <p className="task-error">服务器报告任务处理失败</p>
      ) : null}
      {task.snapshot.waiting_reason ? (
        <p className="waiting-reason">{task.snapshot.waiting_reason}</p>
      ) : null}

      <section className="task-detail-overview" aria-label="检查概览">
        <div className="task-detail-result">
          <span>检查结论</span>
          <strong>{inspectionResultText(task)}</strong>
        </div>
        <div className="task-detail-result">
          <span>检查模式</span>
          <strong>{INSPECTION_MODE_TEXT[inspectionMode]}</strong>
        </div>
        <div className="task-detail-progress">
          <span>任务总进度</span>
          <div className="task-detail-progress-value">
            <progress aria-label="任务总进度" max={100} value={progress} />
            <strong>{progress}%</strong>
          </div>
        </div>
      </section>

      {presentation === "detail" ? <section className="task-detail-media" aria-label="源媒体">
        <div className="task-detail-section-heading">
          <h4>源媒体</h4>
          {!task.frame_source && !mediaKind ? (
            <div className="task-detail-media-actions">
              <a href={sourceUrl} target="_blank" rel="noreferrer">
                {hasVideoLocator ? "打开输入视频" : "打开输入文件"}
              </a>
              <Button
                icon={<EyeOutlined aria-hidden="true" />}
                disabled={mediaLoading}
                loading={mediaLoading}
                onClick={() => void discoverMedia()}
              >
                {mediaLoading ? "正在识别媒体…" : "查看媒体证据"}
              </Button>
            </div>
          ) : null}
        </div>
        {task.frame_source ? (
          <div className="task-detail-source-note" aria-label="视频证据帧来源">
            <p>
              来源：帧 {task.frame_source.frame_index} · {task.frame_source.timestamp_seconds} 秒；仅分析这一帧，不代表整段视频结论。
            </p>
            <p className="evidence-links">
              <a href={evidenceUrl(task.frame_source.video_asset)} target="_blank" rel="noreferrer">打开来源视频</a>
              <a href={sourceUrl} target="_blank" rel="noreferrer">打开证据帧</a>
            </p>
          </div>
        ) : null}
        {!task.frame_source && mediaKind === "video" ? (
          <figure className="task-detail-preview">
            <video controls preload="metadata" src={sourceUrl} aria-label="源检查视频" />
            <figcaption><a href={sourceUrl} target="_blank" rel="noreferrer">在新窗口打开源视频</a></figcaption>
          </figure>
        ) : null}
        {!task.frame_source && mediaKind && mediaKind !== "video" ? (
          <figure className="task-detail-preview">
            <a href={sourceUrl} target="_blank" rel="noreferrer">
              <img src={sourceUrl} alt="源检查图片" loading="lazy" />
            </a>
            <figcaption><a className="source-asset-link" href={sourceUrl} target="_blank" rel="noreferrer">打开源图片</a></figcaption>
          </figure>
        ) : null}
        {!task.frame_source && hasVideoLocator && !mediaKind ? (
          <p className="task-detail-media-hint">已保存视频时间定位证据，可在下方展开候选时间线。</p>
        ) : null}
        {mediaError ? <p className="task-error" role="alert">{mediaError}</p> : null}
      </section> : null}

      {presentation !== "workbench" && task.snapshot.findings.length > 0 ? (
        <section className="findings task-detail-findings" aria-labelledby={`findings-${task.snapshot.task_id}`}>
          <h4 id={`findings-${task.snapshot.task_id}`}>发现项</h4>
          <ul>
            {task.snapshot.findings.map((finding) => (
              <li key={finding.finding_id}>
                <div className="task-detail-finding-main">
                  <strong>
                    {finding.canonical_label ?? "未分类"}
                    {finding.open_label ? ` · ${finding.open_label}` : ""}
                  </strong>
                  <Tag>{REVIEW_TEXT[finding.review_status]}</Tag>
                </div>
                <div className="task-detail-finding-meta">
                  {finding.score === null ? null : <span>原始检测器分数 {finding.score}</span>}
                  {finding.confirmed_by ? <span>{CONFIRMATION_TEXT[finding.confirmed_by]}</span> : null}
                  <code title="对话可引用的发现 ID">{finding.finding_id}</code>
                </div>
                <span className="evidence-links">
                  {finding.evidence.map((evidence, index) => (
                    <a
                      key={`${evidence.asset.asset_id}-${evidence.asset.version}-${index}`}
                      href={evidenceUrl(evidence.asset)}
                      target="_blank"
                      rel="noreferrer"
                      className="finding-evidence-link"
                    >
                      {evidence.region && !(evidence.frame_index !== null &&
                        evidence.asset.asset_id === task.input_asset.asset_id &&
                        evidence.asset.version === task.input_asset.version) ? (
                        <EvidenceThumbnail url={evidenceUrl(evidence.asset)} region={evidence.region} />
                      ) : null}
                      原始证据 {index + 1}
                      {evidence.timestamp_seconds === null ? "" : ` · ${evidence.timestamp_seconds} 秒`}
                    </a>
                  ))}
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <section className="task-detail-execution" aria-labelledby={`execution-${task.snapshot.task_id}`}>
        <h4 id={`execution-${task.snapshot.task_id}`}>执行详情</h4>
        <dl className="task-meta">
          <div><dt>阶段</dt><dd>{taskStageText(task)}</dd></div>
          <div>
            <dt>活动工具</dt>
            <dd>{task.snapshot.active_tool ? ACTIVE_TOOL_TEXT[task.snapshot.active_tool] ?? task.snapshot.active_tool : "—"}</dd>
          </div>
          <div><dt>重试次数</dt><dd>{task.snapshot.retry_count}</dd></div>
          <div><dt>已处理帧</dt><dd>{task.snapshot.processed_frames}</dd></div>
          <div><dt>已处理块</dt><dd>{task.snapshot.processed_chunks}</dd></div>
          <div>
            <dt>任务编号</dt>
            <dd title={task.snapshot.task_id}>{task.snapshot.task_id.slice(0, 8)}</dd>
          </div>
        </dl>
      </section>

      {reviewOpen ? (
        <ReviewPanel
          key={task.snapshot.task_id}
          taskId={task.snapshot.task_id}
          snapshotRevision={task.snapshot.updated_at}
          runtimeStatus={task.snapshot.runtime_status}
          sourceAsset={task.input_asset}
          onAccepted={(snapshot) => onReviewed(task, snapshot)}
        />
      ) : null}

      <section className="task-detail-evidence" aria-label="证据与分析工具">
        <h4>证据与分析工具</h4>
        <EnhancementPanel
          taskId={task.snapshot.task_id}
          snapshotRevision={task.snapshot.updated_at}
          runtimeStatus={task.snapshot.runtime_status}
        />
        <GeometryPanel
          taskId={task.snapshot.task_id}
          snapshotRevision={task.snapshot.updated_at}
          runtimeStatus={task.snapshot.runtime_status}
          findingIds={task.snapshot.findings.map((finding) => finding.finding_id)}
        />
        {task.frame_source || (mediaKind !== null && mediaKind !== "video") ? (
          <ImageEvidencePanel
            taskId={task.snapshot.task_id}
            snapshotRevision={task.snapshot.updated_at}
          />
        ) : null}
        {presentation !== "workbench" && !task.frame_source && (hasVideoLocator || mediaKind === "video") ? (
          <VideoTimeline
            taskId={task.snapshot.task_id}
            snapshotRevision={task.snapshot.updated_at}
            onSelectFrame={(frame) => onSelectFrame(task.snapshot.task_id, frame)}
            frameSelectionDisabled={frameSelectionDisabled}
          />
        ) : null}
      </section>
    </article>
  );
}
