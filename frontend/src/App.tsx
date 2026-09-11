import { type FormEvent, useEffect, useMemo, useRef, useState } from "react";

import {
  ApiError,
  cancelTask,
  createSession,
  createTask,
  createTurn,
  getSession,
  getAsset,
  getTask,
  getTurn,
  listTurns,
  startTask,
  uploadAsset,
} from "./api";
import type {
  AssetRef,
  RuntimeStatus,
  SessionRecord,
  TaskRecord,
  TurnRecord,
  TurnSubmission,
} from "./types";
import { ReviewPanel } from "./ReviewPanel";
import { VideoTimeline } from "./VideoTimeline";

const SESSION_KEY = "pipe-inspector.session-id";
const ACTIVE_STATUSES = new Set<RuntimeStatus>(["queued", "running", "waiting_review"]);
const ACTIVE_TURN_STATUSES = new Set(["queued", "running"]);

const RESULT_TEXT = { pending: "待定", normal: "正常", defects_found: "发现缺陷", inconclusive: "无法确定" } as const;
const REVIEW_TEXT = { tentative: "暂定", confirmed: "已确认", rejected: "已排除", needs_review: "需复核" } as const;

const STATUS_TEXT: Record<RuntimeStatus, string> = {
  queued: "等待处理",
  running: "处理中",
  waiting_review: "等待人工复核",
  completed: "已完成",
  partial: "部分完成",
  failed: "处理失败",
  cancelled: "已取消",
};
const TURN_STAGE_TEXT = {
  queued: "等待处理",
  planning: "正在规划",
  executing: "正在执行",
  finished: "已结束",
} as const;

type PendingSubmission = {
  file: File;
  sessionId: string;
  instruction: string;
  requestId: string;
  inputAsset: AssetRef;
};

type PendingTurn = TurnSubmission & {
  file: File | null;
  sessionId: string;
};

type AppProps = {
  pollIntervalMs?: number;
};

export function isActiveTask(task: TaskRecord): boolean {
  return ACTIVE_STATUSES.has(task.snapshot.runtime_status);
}

function isTerminalTask(task: TaskRecord): boolean {
  return !isActiveTask(task);
}

function isActiveTurn(turn: TurnRecord): boolean {
  return ACTIVE_TURN_STATUSES.has(turn.status);
}

function formatError(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return "发生未知错误，请稍后重试";
}

function upsertTask(tasks: TaskRecord[], next: TaskRecord): TaskRecord[] {
  const index = tasks.findIndex(
    (task) =>
      task.snapshot.task_id === next.snapshot.task_id || task.request_id === next.request_id,
  );
  if (index < 0) return [next, ...tasks];
  return tasks.map((task, taskIndex) => (taskIndex === index ? next : task));
}

function upsertTurn(turns: TurnRecord[], next: TurnRecord): TurnRecord[] {
  const index = turns.findIndex((turn) => turn.request_id === next.request_id);
  if (index < 0) return [...turns, next];
  return turns.map((turn, turnIndex) => (turnIndex === index ? next : turn));
}

function evidenceUrl(asset: AssetRef): string {
  return `/api/assets/${encodeURIComponent(asset.asset_id)}/versions/${asset.version}/content`;
}

function reportUrl(taskId: string, format: "json" | "html"): string {
  return `/api/tasks/${encodeURIComponent(taskId)}/report?format=${format}`;
}

function linkedTaskStatus(task: TaskRecord | undefined): string {
  if (!task || isActiveTask(task)) return "对话已回复；关联检查仍在继续";
  if (task.snapshot.runtime_status === "completed") return "关联检查已完成";
  if (task.snapshot.runtime_status === "partial") return "关联检查仅部分完成";
  if (task.snapshot.runtime_status === "failed") return "关联检查已失败";
  return "关联检查已取消";
}

type TaskCardProps = {
  task: TaskRecord;
  starting: boolean;
  cancelling: boolean;
  onStart: (taskId: string) => void;
  onCancel: (taskId: string) => void;
  onReviewed: (task: TaskRecord, snapshot: TaskRecord["snapshot"]) => void;
};

function TaskCard({ task, starting, cancelling, onStart, onCancel, onReviewed }: TaskCardProps) {
  const [reviewOpen, setReviewOpen] = useState(false);
  const [mediaKind, setMediaKind] = useState<string | null>(null);
  const [mediaLoading, setMediaLoading] = useState(false);
  const [mediaError, setMediaError] = useState<string | null>(null);
  const progress = Math.round(
    Math.min(1, Math.max(0, task.snapshot.overall_progress)) * 100,
  );
  const queued = task.snapshot.runtime_status === "queued";
  const waitingReview = task.snapshot.runtime_status === "waiting_review";
  const hasVideoLocator = task.snapshot.findings.some((finding) =>
    finding.evidence.some((evidence) => evidence.frame_index !== null),
  );

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

  return (
    <article className="task-card">
      <div className="task-card__heading">
        <div>
          <p className="eyebrow">{task.parent_task_id ? "关联追加复检" : "检查任务"}</p>
          <h3>{task.instruction}</h3>
        </div>
        <span className={`status status--${task.snapshot.runtime_status}`}>
          {STATUS_TEXT[task.snapshot.runtime_status]}
        </span>
      </div>
      {task.parent_task_id ? (
        <p className="task-hint">
          复用原图片和检测证据，原结果保持不变。{" "}
          <a href={reportUrl(task.parent_task_id, "html")} target="_blank" rel="noreferrer">原任务报告</a>
        </p>
      ) : null}
      {queued ? (
        <>
          <p className="truth-note">{task.parent_task_id ? "复检已入队，等待执行；重复启动沿用同一任务" : "已上传并入队，等待启动检查"}</p>
          <button
            type="button"
            disabled={starting}
            onClick={() => onStart(task.snapshot.task_id)}
          >
            {starting ? "正在启动…" : task.parent_task_id ? "启动关联复检" : "启动检查"}
          </button>
        </>
      ) : null}
      {task.snapshot.runtime_status === "running" ? (
        <p className="truth-note">{task.parent_task_id ? "关联复检正在执行（不重新检测）" : "检查正在执行"}</p>
      ) : null}
      {waitingReview ? (
        <button type="button" onClick={() => setReviewOpen((open) => !open)}>
          {reviewOpen ? "收起人工复核" : "开始人工复核"}
        </button>
      ) : null}
      {task.snapshot.can_cancel === true && !isTerminalTask(task) ? (
        <button
          type="button"
          className="secondary-button"
          disabled={cancelling}
          onClick={() => onCancel(task.snapshot.task_id)}
        >
          {cancelling ? "正在取消…" : "取消检查"}
        </button>
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
      <div className="progress-row">
        <div
          className="progress-track"
          role="progressbar"
          aria-label="任务总进度"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={progress}
        >
          <span style={{ width: `${progress}%` }} />
        </div>
        <strong>{progress}%</strong>
      </div>
      <dl className="task-meta">
        <div>
          <dt>阶段</dt>
          <dd>{task.snapshot.stage}</dd>
        </div>
        <div>
          <dt>检查结论</dt>
          <dd>{RESULT_TEXT[task.snapshot.inspection_result]}</dd>
        </div>
        <div>
          <dt>活动工具</dt>
          <dd>{task.snapshot.active_tool ?? "—"}</dd>
        </div>
        <div>
          <dt>重试次数</dt>
          <dd>{task.snapshot.retry_count}</dd>
        </div>
        <div>
          <dt>已处理帧</dt>
          <dd>{task.snapshot.processed_frames}</dd>
        </div>
        <div>
          <dt>已处理块</dt>
          <dd>{task.snapshot.processed_chunks}</dd>
        </div>
        <div>
          <dt>任务编号</dt>
          <dd title={task.snapshot.task_id}>{task.snapshot.task_id.slice(0, 8)}</dd>
        </div>
      </dl>
      {task.snapshot.findings.length > 0 ? (
        <div className="findings">
          <h4>发现项</h4>
          <ul>
            {task.snapshot.findings.map((finding) => (
              <li key={finding.finding_id}>
                <strong>
                  {finding.canonical_label ?? "未分类"}
                  {finding.open_label ? ` · ${finding.open_label}` : ""}
                </strong>
                <span>{REVIEW_TEXT[finding.review_status]}</span>
                <span className="evidence-links">
                  {finding.evidence.map((evidence, index) => (
                    <a
                      key={`${evidence.asset.asset_id}-${evidence.asset.version}-${index}`}
                      href={evidenceUrl(evidence.asset)}
                      target="_blank"
                      rel="noreferrer"
                    >
                      原始证据 {index + 1}
                      {evidence.timestamp_seconds === null ? "" : ` · ${evidence.timestamp_seconds} 秒`}
                    </a>
                  ))}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {!waitingReview && isTerminalTask(task) ? (
        <button type="button" className="secondary-button" onClick={() => setReviewOpen((open) => !open)}>
          {reviewOpen ? "收起审核记录" : "查看审核记录"}
        </button>
      ) : null}
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
      {hasVideoLocator || mediaKind === "video" ? (
        <VideoTimeline taskId={task.snapshot.task_id} snapshotRevision={task.snapshot.updated_at} />
      ) : null}
      {!hasVideoLocator && !mediaKind ? (
        <button type="button" className="secondary-button" disabled={mediaLoading} onClick={() => void discoverMedia()}>
          {mediaLoading ? "正在识别媒体…" : "查看媒体证据"}
        </button>
      ) : null}
      {mediaKind && mediaKind !== "video" ? (
        <a className="source-asset-link" href={evidenceUrl(task.input_asset)} target="_blank" rel="noreferrer">打开源图片</a>
      ) : null}
      {mediaError ? <p className="task-error" role="alert">{mediaError}</p> : null}
      {isTerminalTask(task) ? (
        <div className="report-links" aria-label="检查报告">
          <a href={reportUrl(task.snapshot.task_id, "json")} target="_blank" rel="noreferrer">JSON 报告</a>
          <a href={reportUrl(task.snapshot.task_id, "html")} target="_blank" rel="noreferrer">HTML 报告</a>
        </div>
      ) : null}
    </article>
  );
}

type TurnCardProps = {
  turn: TurnRecord;
  linkedTask: TaskRecord | undefined;
  resending: boolean;
  onResend: (turn: TurnRecord) => void;
};

function TurnCard({ turn, linkedTask, resending, onResend }: TurnCardProps) {
  return (
    <article className="turn-card">
      <div className="turn-message turn-message--user">
        <span>你</span>
        <p>{turn.text}</p>
      </div>
      <div className="turn-message turn-message--assistant">
        <span>助手 · {TURN_STAGE_TEXT[turn.stage]}</span>
        {turn.reply ? <p>{turn.reply}</p> : <p className="muted">服务器正在处理这条消息。</p>}
        {turn.status === "failed" ? (
          <p className="task-error">处理失败{turn.error_code ? `：${turn.error_code}` : ""}</p>
        ) : null}
        {turn.status === "queued" ? (
          <button type="button" className="secondary-button" disabled={resending} onClick={() => onResend(turn)}>
            {resending ? "正在确认…" : "确认启动"}
          </button>
        ) : null}
        {turn.status === "completed" && turn.linked_task_id ? (
          <p className="linked-status">{linkedTaskStatus(linkedTask)}</p>
        ) : null}
      </div>
    </article>
  );
}

export default function App({ pollIntervalMs = 2_000 }: AppProps) {
  const [session, setSession] = useState<SessionRecord | null>(null);
  const [tasks, setTasks] = useState<TaskRecord[]>([]);
  const [turns, setTurns] = useState<TurnRecord[]>([]);
  const [instruction, setInstruction] = useState("");
  const [conversationText, setConversationText] = useState("");
  const [conversationFile, setConversationFile] = useState<File | null>(null);
  const [selectedTaskId, setSelectedTaskId] = useState("");
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [turnSubmitting, setTurnSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conversationError, setConversationError] = useState<string | null>(null);
  const [startingTaskIds, setStartingTaskIds] = useState<Set<string>>(() => new Set());
  const [cancellingTaskIds, setCancellingTaskIds] = useState<Set<string>>(() => new Set());
  const fileInput = useRef<HTMLInputElement>(null);
  const form = useRef<HTMLFormElement>(null);
  const conversationForm = useRef<HTMLFormElement>(null);
  const conversationFileInput = useRef<HTMLInputElement>(null);
  const pendingSubmission = useRef<PendingSubmission | null>(null);
  const pendingTurn = useRef<PendingTurn | null>(null);

  async function startNewSession() {
    setLoading(true);
    setError(null);
    try {
      const next = await createSession();
      localStorage.setItem(SESSION_KEY, next.session_id);
      setSession(next);
      setTasks([]);
      setTurns([]);
      pendingTurn.current = null;
    } catch (nextError) {
      setError(formatError(nextError));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    let cancelled = false;
    const savedSessionId = localStorage.getItem(SESSION_KEY);
    const load = savedSessionId
      ? Promise.all([getSession(savedSessionId), listTurns(savedSessionId)])
          .then(([detail, restoredTurns]) => ({ ...detail, turns: restoredTurns }))
      : createSession().then((nextSession) => ({ session: nextSession, tasks: [], turns: [] }));

    void load
      .then((detail) => {
        if (cancelled) return;
        localStorage.setItem(SESSION_KEY, detail.session.session_id);
        setSession(detail.session);
        setTasks(detail.tasks);
        setTurns(detail.turns);
      })
      .catch((nextError: unknown) => {
        if (!cancelled) setError(formatError(nextError));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const activeTurnIds = useMemo(
    () => turns.filter(isActiveTurn).map((turn) => turn.request_id),
    [turns],
  );
  const activeTurnKey = activeTurnIds.join(",");

  useEffect(() => {
    if (!session || !activeTurnKey) return;
    const requestIds = activeTurnKey.split(",");
    let stopped = false;
    let polling = false;
    const timer = window.setInterval(() => {
      if (polling) return;
      polling = true;
      void Promise.all(requestIds.map((requestId) => getTurn(session.session_id, requestId)))
        .then((updates) => {
          if (stopped) return;
          setTurns((current) => updates.reduce(upsertTurn, current));
          if (updates.every((turn) => !isActiveTurn(turn))) {
            stopped = true;
            window.clearInterval(timer);
          }
        })
        .catch((nextError: unknown) => {
          if (!stopped) setConversationError(formatError(nextError));
        })
        .finally(() => {
          polling = false;
        });
    }, pollIntervalMs);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [activeTurnKey, pollIntervalMs, session]);

  const linkedTaskKey = useMemo(
    () => Array.from(new Set(turns.map((turn) => turn.linked_task_id).filter((id): id is string => Boolean(id)))).join(","),
    [turns],
  );
  const knownTaskKey = tasks.map((task) => task.snapshot.task_id).join(",");

  useEffect(() => {
    if (!linkedTaskKey) return;
    const known = new Set(knownTaskKey ? knownTaskKey.split(",") : []);
    const missing = linkedTaskKey.split(",").filter((taskId) => !known.has(taskId));
    if (!missing.length) return;
    let stopped = false;
    void Promise.all(missing.map((taskId) => getTask(taskId)))
      .then((linkedTasks) => {
        if (!stopped) setTasks((current) => linkedTasks.reduce(upsertTask, current));
      })
      .catch((nextError: unknown) => {
        if (!stopped) setError(formatError(nextError));
      });
    return () => {
      stopped = true;
    };
  }, [knownTaskKey, linkedTaskKey]);

  const activeTaskIds = useMemo(
    () => tasks.filter(isActiveTask).map((task) => task.snapshot.task_id),
    [tasks],
  );
  const activeTaskKey = activeTaskIds.join(",");

  useEffect(() => {
    if (!activeTaskKey) return;
    const taskIds = activeTaskKey.split(",");
    let stopped = false;
    let polling = false;
    const timer = window.setInterval(() => {
      if (polling) return;
      polling = true;
      void Promise.all(taskIds.map((taskId) => getTask(taskId)))
        .then((updates) => {
          if (stopped) return;
          setTasks((current) => updates.reduce(upsertTask, current));
        })
        .catch((nextError: unknown) => {
          if (!stopped) setError(formatError(nextError));
        })
        .finally(() => {
          polling = false;
        });
    }, pollIntervalMs);

    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [activeTaskKey, pollIntervalMs]);

  async function startInspection(taskId: string) {
    setStartingTaskIds((current) => new Set(current).add(taskId));
    setError(null);
    try {
      const started = await startTask(taskId);
      setTasks((current) => upsertTask(current, started));
    } catch (nextError) {
      setError(formatError(nextError));
    } finally {
      setStartingTaskIds((current) => {
        const next = new Set(current);
        next.delete(taskId);
        return next;
      });
    }
  }

  async function cancelInspection(taskId: string) {
    setCancellingTaskIds((current) => new Set(current).add(taskId));
    setError(null);
    try {
      const cancelled = await cancelTask(taskId);
      setTasks((current) => upsertTask(current, cancelled));
    } catch (nextError) {
      setError(formatError(nextError));
    } finally {
      setCancellingTaskIds((current) => {
        const next = new Set(current);
        next.delete(taskId);
        return next;
      });
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!session) return;
    const file = fileInput.current?.files?.[0];
    const cleanInstruction = instruction.trim();
    if (!file) {
      setError("请选择现场图片或视频");
      return;
    }
    if (!cleanInstruction) {
      setError("请输入检查要求");
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      let pending = pendingSubmission.current;
      if (
        !pending ||
        pending.file !== file ||
        pending.sessionId !== session.session_id ||
        pending.instruction !== cleanInstruction
      ) {
        pendingSubmission.current = null;
        const uploaded = await uploadAsset(file);
        pending = {
          file,
          sessionId: session.session_id,
          instruction: cleanInstruction,
          requestId: crypto.randomUUID(),
          inputAsset: uploaded.ref,
        };
        pendingSubmission.current = pending;
      }
      const created = await createTask({
        request_id: pending.requestId,
        session_id: session.session_id,
        input_asset: pending.inputAsset,
        instruction: cleanInstruction,
      });
      pendingSubmission.current = null;
      setTasks((current) => upsertTask(current, created));
      setInstruction("");
      form.current?.reset();
    } catch (nextError) {
      setError(formatError(nextError));
    } finally {
      setSubmitting(false);
    }
  }

  async function postTurn(submission: PendingTurn | TurnRecord) {
    if (!session) return;
    setTurnSubmitting(true);
    setConversationError(null);
    try {
      const created = await createTurn(session.session_id, {
        request_id: submission.request_id,
        text: submission.text,
        input_asset: submission.input_asset,
        task_id: submission.task_id,
      });
      setTurns((current) => upsertTurn(current, created));
      pendingTurn.current = null;
      setConversationText("");
      setConversationFile(null);
      setSelectedTaskId("");
      conversationForm.current?.reset();
    } catch (nextError) {
      const message = formatError(nextError);
      setConversationError(
        nextError instanceof ApiError && nextError.status === 0
          ? `${message}。再次发送将使用相同请求编号，不会重复上传附件。`
          : message,
      );
    } finally {
      setTurnSubmitting(false);
    }
  }

  async function submitTurn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!session || activeTurnIds.length > 0) return;
    const cleanText = conversationText.trim();
    if (!cleanText) {
      setConversationError("请输入对话内容");
      return;
    }
    const taskId = selectedTaskId || null;
    let pending = pendingTurn.current;
    const sameIntent = Boolean(
      pending && pending.sessionId === session.session_id && pending.text === cleanText &&
      pending.task_id === taskId && pending.file === conversationFile,
    );
    if (!sameIntent) {
      setTurnSubmitting(true);
      setConversationError(null);
      try {
        const reusableAsset = pending?.file === conversationFile ? pending.input_asset : null;
        const inputAsset = reusableAsset ?? (conversationFile ? (await uploadAsset(conversationFile)).ref : null);
        pending = {
          request_id: crypto.randomUUID(),
          sessionId: session.session_id,
          text: cleanText,
          input_asset: inputAsset,
          task_id: taskId,
          file: conversationFile,
        };
        pendingTurn.current = pending;
      } catch (nextError) {
        setConversationError(formatError(nextError));
        setTurnSubmitting(false);
        return;
      }
      setTurnSubmitting(false);
    }
    if (pending) await postTurn(pending);
  }

  const hasActiveTurn = activeTurnIds.length > 0;
  const pendingMatchesComposer = Boolean(
    pendingTurn.current &&
    pendingTurn.current.sessionId === session?.session_id &&
    pendingTurn.current.text === conversationText.trim() &&
    pendingTurn.current.task_id === (selectedTaskId || null) &&
    pendingTurn.current.file === conversationFile,
  );

  return (
    <main className="app-shell">
      <header className="masthead">
        <div>
          <p className="eyebrow">PIPE INSPECTOR · 本地工作台</p>
          <h1>管道影像检查</h1>
          <p className="subtitle">提交现场证据与检查要求，并查看服务器返回的任务状态。</p>
        </div>
        <div className="session-panel" aria-label="当前会话">
          <span>当前会话</span>
          <strong>{session?.session_id ?? "正在连接…"}</strong>
        </div>
      </header>

      {error ? (
        <div className="error-banner" role="alert">
          <span>{error}</span>
          {!session && !loading ? (
            <button type="button" onClick={() => void startNewSession()}>
              新建会话
            </button>
          ) : null}
        </div>
      ) : null}

      <section className="conversation-panel" aria-labelledby="conversation-title">
        <div className="tasks__heading">
          <div>
            <p className="eyebrow">持久对话</p>
            <h2 id="conversation-title">检查助手</h2>
          </div>
          <span>{turns.length} 条</span>
        </div>
        <p className="conversation-note">
          可询问已存证据、请求报告，或上传图片/视频启动检查。每个已完成或部分完成的原图片任务可追加一次 VLM 复检；其他工具尚未接入。
          附件与已有任务二选一，选择一项会清除另一项。
        </p>
        <div className="transcript" aria-live="polite">
          {!loading && turns.length === 0 ? <p className="empty-state">尚无对话消息。</p> : null}
          {turns.map((turn) => (
            <TurnCard
              key={turn.request_id}
              turn={turn}
              linkedTask={tasks.find((task) => task.snapshot.task_id === turn.linked_task_id)}
              resending={turnSubmitting}
              onResend={(nextTurn) => void postTurn(nextTurn)}
            />
          ))}
        </div>
        {conversationError ? <div className="conversation-error" role="alert">{conversationError}</div> : null}
        <form ref={conversationForm} className="conversation-form" onSubmit={(event) => void submitTurn(event)}>
          <label className="field">
            <span>对话内容</span>
            <textarea
              value={conversationText}
              onChange={(event) => setConversationText(event.target.value)}
              maxLength={4_000}
              rows={3}
              placeholder="例如：请检查这份影像，或解释上一项检查结果"
              disabled={!session || turnSubmitting || hasActiveTurn}
            />
          </label>
          <div className="conversation-options">
            <label className="field">
              <span>对话附件（可选）</span>
              <input
                ref={conversationFileInput}
                type="file"
                accept=".jpg,.jpeg,.png,.mp4,.mov,.avi,.mkv,image/jpeg,image/png,video/*"
                onChange={(event) => {
                  const nextFile = event.target.files?.[0] ?? null;
                  setConversationFile(nextFile);
                  if (nextFile) setSelectedTaskId("");
                }}
                disabled={!session || turnSubmitting || hasActiveTurn}
              />
            </label>
            <label className="field">
              <span>关联已有任务（可选）</span>
              <select
                value={selectedTaskId}
                onChange={(event) => {
                  setSelectedTaskId(event.target.value);
                  if (event.target.value) {
                    setConversationFile(null);
                    if (conversationFileInput.current) conversationFileInput.current.value = "";
                  }
                }}
                disabled={!session || turnSubmitting || hasActiveTurn}
              >
                <option value="">不关联任务</option>
                {tasks.map((task) => (
                  <option key={task.snapshot.task_id} value={task.snapshot.task_id}>
                    {task.snapshot.task_id.slice(0, 8)} · {task.instruction}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="form-footer">
            <small>{hasActiveTurn ? "当前消息完成后可继续对话" : `${conversationText.length} / 4000`}</small>
            <button type="submit" disabled={!session || turnSubmitting || hasActiveTurn}>
              {turnSubmitting ? "正在发送…" : pendingMatchesComposer ? "重新发送" : "发送消息"}
            </button>
          </div>
        </form>
      </section>

      <section className="intake-panel" aria-labelledby="intake-title">
        <div className="section-copy">
          <p className="eyebrow">新建检查</p>
          <h2 id="intake-title">提交现场资料</h2>
          <p>支持 JPEG、PNG、MP4、MOV、AVI 与 MKV。文件是否可用以服务器校验为准。</p>
          <p>此入口只保存资料，需手动启动基础检查；按文本规划工具请使用上方检查助手。</p>
        </div>
        <form ref={form} onSubmit={(event) => void submit(event)}>
          <label className="field">
            <span>现场文件</span>
            <input
              ref={fileInput}
              type="file"
              accept=".jpg,.jpeg,.png,.mp4,.mov,.avi,.mkv,image/jpeg,image/png,video/*"
              disabled={!session || submitting}
            />
          </label>
          <label className="field">
            <span>检查要求</span>
            <textarea
              value={instruction}
              onChange={(event) => setInstruction(event.target.value)}
              maxLength={4_000}
              rows={4}
              placeholder="例如：请检查这段管道是否存在破损或堵塞"
              disabled={!session || submitting}
            />
          </label>
          <div className="form-footer">
            <small>{instruction.length} / 4000</small>
            <button type="submit" disabled={!session || submitting}>
              {submitting ? "正在提交…" : "提交检查"}
            </button>
          </div>
        </form>
      </section>

      <section className="tasks" aria-labelledby="tasks-title">
        <div className="tasks__heading">
          <div>
            <p className="eyebrow">服务器状态</p>
            <h2 id="tasks-title">任务记录</h2>
          </div>
          <span>{tasks.length} 项</span>
        </div>
        {loading ? <p className="empty-state">正在恢复会话…</p> : null}
        {!loading && tasks.length === 0 ? (
          <p className="empty-state">尚未提交检查任务。</p>
        ) : null}
        <div className="task-grid">
          {tasks.map((task) => (
            <TaskCard
              key={task.snapshot.task_id}
              task={task}
              starting={startingTaskIds.has(task.snapshot.task_id)}
              cancelling={cancellingTaskIds.has(task.snapshot.task_id)}
              onStart={(taskId) => void startInspection(taskId)}
              onCancel={(taskId) => void cancelInspection(taskId)}
              onReviewed={(reviewedTask, snapshot) => {
                setTasks((current) => upsertTask(current, { ...reviewedTask, snapshot }));
              }}
            />
          ))}
        </div>
      </section>
    </main>
  );
}
