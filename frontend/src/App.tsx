import { type FormEvent, useEffect, useMemo, useRef, useState } from "react";

import { ApiError, createSession, createTask, getSession, getTask, startTask, uploadAsset } from "./api";
import type { AssetRef, RuntimeStatus, SessionRecord, TaskRecord } from "./types";

const SESSION_KEY = "pipe-inspector.session-id";
const ACTIVE_STATUSES = new Set<RuntimeStatus>(["queued", "running", "waiting_review"]);

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

type PendingSubmission = {
  file: File;
  sessionId: string;
  instruction: string;
  requestId: string;
  inputAsset: AssetRef;
};

type AppProps = {
  pollIntervalMs?: number;
};

export function isActiveTask(task: TaskRecord): boolean {
  return ACTIVE_STATUSES.has(task.snapshot.runtime_status);
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

function evidenceUrl(asset: AssetRef): string {
  return `/api/assets/${encodeURIComponent(asset.asset_id)}/versions/${asset.version}/content`;
}

type TaskCardProps = {
  task: TaskRecord;
  starting: boolean;
  onStart: (taskId: string) => void;
};

function TaskCard({ task, starting, onStart }: TaskCardProps) {
  const progress = Math.round(
    Math.min(1, Math.max(0, task.snapshot.overall_progress)) * 100,
  );
  const queued = task.snapshot.runtime_status === "queued";

  return (
    <article className="task-card">
      <div className="task-card__heading">
        <div>
          <p className="eyebrow">检查任务</p>
          <h3>{task.instruction}</h3>
        </div>
        <span className={`status status--${task.snapshot.runtime_status}`}>
          {STATUS_TEXT[task.snapshot.runtime_status]}
        </span>
      </div>
      {queued ? (
        <>
          <p className="truth-note">已上传并入队，等待启动图片检查</p>
          <p className="task-hint">当前仅图片可执行；视频执行尚未接入。</p>
          <button
            type="button"
            disabled={starting}
            onClick={() => onStart(task.snapshot.task_id)}
          >
            {starting ? "正在启动…" : "启动图片检查"}
          </button>
        </>
      ) : null}
      {task.snapshot.runtime_status === "running" ? (
        <p className="truth-note">图片检查正在执行</p>
      ) : null}
      {task.snapshot.runtime_status === "partial" ? (
        <p className="truth-note">检查部分完成；未解决项尚无人工处理入口</p>
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
                    </a>
                  ))}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </article>
  );
}

export default function App({ pollIntervalMs = 2_000 }: AppProps) {
  const [session, setSession] = useState<SessionRecord | null>(null);
  const [tasks, setTasks] = useState<TaskRecord[]>([]);
  const [instruction, setInstruction] = useState("");
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [startingTaskIds, setStartingTaskIds] = useState<Set<string>>(() => new Set());
  const fileInput = useRef<HTMLInputElement>(null);
  const form = useRef<HTMLFormElement>(null);
  const pendingSubmission = useRef<PendingSubmission | null>(null);

  async function startNewSession() {
    setLoading(true);
    setError(null);
    try {
      const next = await createSession();
      localStorage.setItem(SESSION_KEY, next.session_id);
      setSession(next);
      setTasks([]);
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
      ? getSession(savedSessionId)
      : createSession().then((nextSession) => ({ session: nextSession, tasks: [] }));

    void load
      .then((detail) => {
        if (cancelled) return;
        localStorage.setItem(SESSION_KEY, detail.session.session_id);
        setSession(detail.session);
        setTasks(detail.tasks);
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

      <section className="intake-panel" aria-labelledby="intake-title">
        <div className="section-copy">
          <p className="eyebrow">新建检查</p>
          <h2 id="intake-title">提交现场资料</h2>
          <p>支持 JPEG、PNG、MP4、MOV、AVI 与 MKV。文件是否可用以服务器校验为准。</p>
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
              onStart={(taskId) => void startInspection(taskId)}
            />
          ))}
        </div>
      </section>
    </main>
  );
}
