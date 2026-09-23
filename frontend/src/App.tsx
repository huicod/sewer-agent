import { Button, ConfigProvider, Input, Layout, Modal, Spin } from "antd";
import zhCN from "antd/locale/zh_CN";
import { FileSearchOutlined, InfoCircleOutlined, MenuOutlined, MessageOutlined, UploadOutlined } from "@ant-design/icons";
import { WorkspaceSidebar } from "./WorkspaceSidebar";
import { TaskCard } from "./TaskCard";
import { type FormEvent, useEffect, useMemo, useRef, useState } from "react";

import {
  ApiError,
  cancelTask,
  createSession,
  createTask,
  createTurn,
  getSession,
  getTask,
  getTurn,
  listTurns,
  startTask,
  uploadAsset,
} from "./api";
import type {
  AssetRef,
  InspectionMode,
  RuntimeStatus,
  SessionRecord,
  TaskRecord,
  TurnRecord,
  TurnSubmission,
  VideoEvidenceFrame,
} from "./types";
import { ComparisonPanel } from "./ComparisonPanel";

const SESSION_KEY = "pipe-inspector.session-id";
const ACTIVE_STATUSES = new Set<RuntimeStatus>(["queued", "running", "waiting_review"]);
const ACTIVE_TURN_STATUSES = new Set(["queued", "running"]);

const TURN_STAGE_TEXT = {
  queued: "等待处理",
  planning: "正在规划",
  executing: "正在执行",
  finished: "已结束",
} as const;
const DEFAULT_INSPECTION_MODE: InspectionMode = "yolo_vlm";
const INSPECTION_MODE_TEXT: Record<InspectionMode, string> = {
  yolo_vlm: "YOLO＋VLM 复检",
  yolo_only: "纯 YOLO（不复检）",
};

type PendingSubmission = {
  file: File;
  sessionId: string;
  instruction: string;
  requestId: string;
  inputAsset: AssetRef;
  inspectionMode: InspectionMode;
};

type PendingTurn = TurnSubmission & {
  file: File | null;
  frameAsset: AssetRef | null;
  sessionId: string;
};

type SelectedVideoFrame = {
  parentTaskId: string;
  frame: VideoEvidenceFrame;
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

function sameAssetRef(left: AssetRef | null, right: AssetRef | null): boolean {
  if (left === null || right === null) return left === right;
  return left.asset_id === right.asset_id && left.version === right.version;
}

function inspectionModeOf(record: { inspection_mode?: InspectionMode }): InspectionMode {
  return record.inspection_mode ?? DEFAULT_INSPECTION_MODE;
}

function isImageFile(file: File | null): boolean {
  if (!file) return false;
  if (file.type.startsWith("image/")) return true;
  if (file.type.startsWith("video/")) return false;
  return /\.(?:jpe?g|png)$/i.test(file.name);
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

function linkedTaskStatus(task: TaskRecord | undefined): string {
  if (!task || isActiveTask(task)) return "对话已回复；关联检查仍在继续";
  if (task.snapshot.runtime_status === "completed") return "关联检查已完成";
  if (task.snapshot.runtime_status === "partial") return "关联检查仅部分完成";
  if (task.snapshot.runtime_status === "failed") return "关联检查已失败";
  return "关联检查已取消";
}

type InspectionModeFieldProps = {
  value: InspectionMode;
  disabled: boolean;
  disabledReason: string;
  onChange: (mode: InspectionMode) => void;
};

function InspectionModeField({
  value,
  disabled,
  disabledReason,
  onChange,
}: InspectionModeFieldProps) {
  return (
    <label className="field">
      <span>检查模式</span>
      <select
        aria-label="检查模式"
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value as InspectionMode)}
      >
        <option value="yolo_vlm">YOLO＋VLM 复检</option>
        <option value="yolo_only">纯 YOLO（不复检）</option>
      </select>
      <small>{disabled ? disabledReason : "纯 YOLO 跳过 VLM 复检，结果会明确标注未复检。"}</small>
    </label>
  );
}

type TurnCardProps = {
  turn: TurnRecord;
  sessionId: string;
  linkedTask: TaskRecord | undefined;
  resending: boolean;
  onResend: (turn: TurnRecord) => void;
};

function TurnCard({ turn, sessionId, linkedTask, resending, onResend }: TurnCardProps) {
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
          <Button htmlType="button" className="secondary-button" disabled={resending} onClick={() => onResend(turn)}>
            {resending ? "正在确认…" : "确认启动"}
          </Button>
        ) : null}
        {turn.status === "completed" && turn.linked_task_id ? (
          <p className="linked-status">{linkedTaskStatus(linkedTask)}</p>
        ) : null}
        {turn.status === "completed" ? (
          <ComparisonPanel sessionId={sessionId} requestId={turn.request_id} />
        ) : null}
      </div>
    </article>
  );
}

function Workbench({ pollIntervalMs = 2_000 }: AppProps) {
  const [focusedTaskId, setFocusedTaskId] = useState("");
  const [intakeOpen, setIntakeOpen] = useState(false);
  const [assistantOpen, setAssistantOpen] = useState(true);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [session, setSession] = useState<SessionRecord | null>(null);
  const [tasks, setTasks] = useState<TaskRecord[]>([]);
  const [turns, setTurns] = useState<TurnRecord[]>([]);
  const [instruction, setInstruction] = useState("");
  const [directFile, setDirectFile] = useState<File | null>(null);
  const [inspectionMode, setInspectionMode] = useState<InspectionMode>(DEFAULT_INSPECTION_MODE);
  const [conversationText, setConversationText] = useState("");
  const [conversationFile, setConversationFile] = useState<File | null>(null);
  const [conversationInspectionMode, setConversationInspectionMode] =
    useState<InspectionMode>(DEFAULT_INSPECTION_MODE);
  const [selectedTaskId, setSelectedTaskId] = useState("");
  const [selectedReferenceTaskId, setSelectedReferenceTaskId] = useState("");
  const [selectedVideoFrame, setSelectedVideoFrame] = useState<SelectedVideoFrame | null>(null);
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
  const navigationToggle = useRef<HTMLButtonElement>(null);

  function closeNavigation() {
    setSidebarOpen(false);
    navigationToggle.current?.focus();
  }

  useEffect(() => {
    if (!sidebarOpen) return;
    const focusFrame = requestAnimationFrame(() => {
      document.querySelector<HTMLButtonElement>(".workspace-sidebar__new")?.focus({ preventScroll: true });
    });
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeNavigation();
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      cancelAnimationFrame(focusFrame);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [sidebarOpen]);

  async function startNewSession() {
    setLoading(true);
    setError(null);
    try {
      const next = await createSession();
      localStorage.setItem(SESSION_KEY, next.session_id);
      setSession(next);
      setTasks([]);
      setFocusedTaskId("");
      setTurns([]);
      setDirectFile(null);
      setInspectionMode(DEFAULT_INSPECTION_MODE);
      setConversationFile(null);
      setConversationInspectionMode(DEFAULT_INSPECTION_MODE);
      setSelectedTaskId("");
      setSelectedReferenceTaskId("");
      setSelectedVideoFrame(null);
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
        pending.instruction !== cleanInstruction ||
        pending.inspectionMode !== inspectionMode
      ) {
        const reusableAsset = pending && pending.file === file &&
          pending.sessionId === session.session_id && pending.instruction === cleanInstruction
          ? pending.inputAsset
          : null;
        const inputAsset = reusableAsset ?? (await uploadAsset(file)).ref;
        pending = {
          file,
          sessionId: session.session_id,
          instruction: cleanInstruction,
          requestId: crypto.randomUUID(),
          inputAsset,
          inspectionMode,
        };
        pendingSubmission.current = pending;
      }
      const created = await createTask({
        request_id: pending.requestId,
        session_id: session.session_id,
        input_asset: pending.inputAsset,
        instruction: cleanInstruction,
        inspection_mode: pending.inspectionMode,
      });
      pendingSubmission.current = null;
      setTasks((current) => upsertTask(current, created));
      setFocusedTaskId(created.snapshot.task_id);
      setIntakeOpen(false);
      setInstruction("");
      setDirectFile(null);
      setInspectionMode(DEFAULT_INSPECTION_MODE);
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
        inspection_mode: inspectionModeOf(submission),
        ...(submission.reference_task_id
          ? { reference_task_id: submission.reference_task_id }
          : {}),
      });
      setTurns((current) => upsertTurn(current, created));
      pendingTurn.current = null;
      setConversationText("");
      setConversationFile(null);
      setConversationInspectionMode(DEFAULT_INSPECTION_MODE);
      setSelectedTaskId("");
      setSelectedReferenceTaskId("");
      setSelectedVideoFrame(null);
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
    const taskId = selectedVideoFrame?.parentTaskId ?? (selectedTaskId || null);
    const referenceTaskId = selectedReferenceTaskId || null;
    if (referenceTaskId && (conversationFile || selectedVideoFrame)) {
      setConversationError("保存结果对比不能同时上传新附件");
      return;
    }
    const selectedFrameAsset = selectedVideoFrame?.frame.asset ?? null;
    let pending = pendingTurn.current;
    const sameIntent = Boolean(
      pending && pending.sessionId === session.session_id && pending.text === cleanText &&
      pending.task_id === taskId && pending.file === conversationFile &&
      pending.reference_task_id === referenceTaskId &&
      inspectionModeOf(pending) === conversationInspectionMode &&
      sameAssetRef(pending.frameAsset, selectedFrameAsset),
    );
    if (!sameIntent) {
      setTurnSubmitting(true);
      setConversationError(null);
      try {
        const reusableAsset = conversationFile !== null && pending?.file === conversationFile &&
          pending.frameAsset === null ? pending.input_asset : null;
        const inputAsset = selectedFrameAsset ?? reusableAsset ??
          (conversationFile ? (await uploadAsset(conversationFile)).ref : null);
        pending = {
          request_id: crypto.randomUUID(),
          sessionId: session.session_id,
          text: cleanText,
          input_asset: inputAsset,
          task_id: taskId,
          reference_task_id: referenceTaskId,
          inspection_mode: conversationInspectionMode,
          file: conversationFile,
          frameAsset: selectedFrameAsset,
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
    pendingTurn.current.reference_task_id === (selectedReferenceTaskId || null) &&
    inspectionModeOf(pendingTurn.current) === conversationInspectionMode &&
    pendingTurn.current.file === conversationFile &&
    sameAssetRef(pendingTurn.current.frameAsset, selectedVideoFrame?.frame.asset ?? null),
  );

  function selectVideoFrame(parentTaskId: string, frame: VideoEvidenceFrame) {
    setAssistantOpen(true);
    setSelectedVideoFrame({ parentTaskId, frame });
    setSelectedTaskId(parentTaskId);
    setSelectedReferenceTaskId("");
    setConversationFile(null);
    setConversationInspectionMode(DEFAULT_INSPECTION_MODE);
    if (conversationFileInput.current) conversationFileInput.current.value = "";
    pendingTurn.current = null;
  }

  const focusedTask = tasks.find((task) => task.snapshot.task_id === focusedTaskId) ?? tasks[0];

  return (
    <Layout className="workbench">
      {sidebarOpen ? <button className="sidebar-backdrop" aria-label="关闭任务导航" onClick={closeNavigation} /> : null}
      <Layout.Sider id="task-navigation" width={248} theme="light" className={`workspace-sider${sidebarOpen ? " workspace-sider--open" : ""}`}>
        <WorkspaceSidebar
          tasks={tasks}
          activeTaskId={focusedTask?.snapshot.task_id ?? ""}
          loading={loading}
          onSelectTask={(id) => { setFocusedTaskId(id); if (sidebarOpen) closeNavigation(); }}
          onNewInspection={() => { setIntakeOpen(true); setSidebarOpen(false); }}
        />
      </Layout.Sider>
      <Layout className="workspace-body">
        <Layout.Header className="workspace-header">
          <div className="workspace-header__title">
            <Button ref={navigationToggle} className="mobile-menu" type="text" icon={<MenuOutlined aria-hidden="true" />} aria-label="打开任务导航" aria-expanded={sidebarOpen} aria-controls="task-navigation" onClick={() => setSidebarOpen(true)} />
            <h1>管道影像检查</h1><span className="workspace-header__divider" /><span className="workspace-header__caption">检查工作台</span>
          </div>
          <div className="workspace-header__actions">
            <span className="workspace-session" aria-label="当前会话"><span>当前会话</span><code>{session?.session_id ?? "正在连接…"}</code></span>
            <Button icon={<MessageOutlined aria-hidden="true" />} aria-label={assistantOpen ? "隐藏检查助手" : "显示检查助手"} onClick={() => setAssistantOpen((open) => !open)}>{assistantOpen ? "收起助手" : "检查助手"}</Button>
          </div>
        </Layout.Header>
        {error && !intakeOpen ? (
          <div className="workspace-error" role="alert">
            <InfoCircleOutlined aria-hidden="true" /><span>{error}</span>
            {!session && !loading ? <Button onClick={() => void startNewSession()}>新建会话</Button> : null}
          </div>
        ) : null}
        <Layout.Content className={`workspace-content${assistantOpen ? "" : " workspace-content--wide"}`}>
          <div className="inspection-workspace">
            <div className="workspace-breadcrumb"><FileSearchOutlined aria-hidden="true" /><span>检查任务</span><span>/</span><span>{focusedTask ? focusedTask.snapshot.task_id.slice(0, 8) : "工作台"}</span></div>
            <section className="inspection-detail" aria-label="检查详情">
              {loading ? <div className="workspace-loading"><Spin /><p>正在恢复会话…</p></div> : focusedTask ? (
                <TaskCard
                  key={focusedTask.snapshot.task_id}
                  task={focusedTask}
                  starting={startingTaskIds.has(focusedTask.snapshot.task_id)}
                  cancelling={cancellingTaskIds.has(focusedTask.snapshot.task_id)}
                  onStart={(taskId) => void startInspection(taskId)}
                  onCancel={(taskId) => void cancelInspection(taskId)}
                  onReviewed={(reviewedTask, snapshot) => {
                    setTasks((current) => upsertTask(current, { ...reviewedTask, snapshot }));
                  }}
                  onSelectFrame={selectVideoFrame}
                  frameSelectionDisabled={turnSubmitting || hasActiveTurn}
                />
              ) : (
                <div className="workspace-empty">
                  <div className="workspace-empty__icon"><FileSearchOutlined aria-hidden="true" /></div>
                  <h2>从一份现场影像开始</h2>
                  <p>上传管道图片或视频，查看检测证据、复核缺陷并导出报告。</p>
                  <Button type="primary" size="large" icon={<UploadOutlined aria-hidden="true" />} onClick={() => setIntakeOpen(true)}>上传现场影像</Button>
                  <span className="workspace-empty__formats">支持 JPG、PNG 及常用视频格式</span>
                  <div className="workspace-empty__steps">
                    <span><b>1</b>上传影像</span><span><b>2</b>检查与复核</span><span><b>3</b>导出报告</span>
                  </div>
                </div>
              )}
            </section>
            <footer className="workspace-footer">{tasks.length} 项检查任务<span>结果以原始证据与复核记录为准</span></footer>
          </div>
          <aside className="assistant-workspace" hidden={!assistantOpen}>
      <section className="conversation-panel" aria-labelledby="conversation-title">
        <div className="tasks__heading">
          <div>
            <MessageOutlined aria-hidden="true" />
            <h2 id="conversation-title">检查助手</h2>
          </div>
          <span>{turns.length} 条</span>
        </div>
        <p className="conversation-note">上传影像发起检查，或关联任务继续追问。</p>
        <details className="assistant-help">
          <summary>可用操作与说明</summary>
          <p>可以询问已存证据、请求报告，或对新图或关联原图提出“必要时按需增强后再检查”。关联原图片可请求一次追加复检或增强。附件与已有任务二选一；对比结果时需选择两个已存任务。</p>
        </details>
        <div className="transcript" aria-live="polite">
          {!loading && turns.length === 0 ? <div className="assistant-empty"><MessageOutlined aria-hidden="true" /><h3>围绕影像，开始检查</h3><p>上传图片或视频，说明检查要求。你也可以关联左侧任务，询问结果或生成报告。</p></div> : null}
          {turns.map((turn) => (
            <TurnCard
              key={turn.request_id}
              turn={turn}
              sessionId={session?.session_id ?? turn.session_id}
              linkedTask={tasks.find((task) => task.snapshot.task_id === turn.linked_task_id)}
              resending={turnSubmitting}
              onResend={(nextTurn) => void postTurn(nextTurn)}
            />
          ))}
        </div>
        {conversationError ? <div className="conversation-error" role="alert">{conversationError}</div> : null}
        <form ref={conversationForm} className="conversation-form" onSubmit={(event) => void submitTurn(event)}>
          {selectedVideoFrame ? (
            <section className="task-hint" aria-label="已选视频证据帧">
              <p>已选视频证据帧：帧 {selectedVideoFrame.frame.frame_index} · {selectedVideoFrame.frame.timestamp_seconds} 秒</p>
              <p className="evidence-links">
                <a href={evidenceUrl(selectedVideoFrame.frame.asset)} target="_blank" rel="noreferrer">打开已选帧</a>
                <Button
                  htmlType="button"
                  className="secondary-button"
                  disabled={turnSubmitting || hasActiveTurn}
                  onClick={() => {
                    setSelectedVideoFrame(null);
                    setSelectedTaskId("");
                    pendingTurn.current = null;
                  }}
                >取消选帧</Button>
              </p>
              <p className="truth-note">
                请在消息中明确要求增强，或提供原图像素 ROI 进行分割/相对测量；整帧相对深度可直接提出。不会自动执行工具。
              </p>
            </section>
          ) : null}
          <label className="field">
            <span id="conversation-input-label">对话内容</span>
            <Input.TextArea
              aria-labelledby="conversation-input-label"
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
                  pendingTurn.current = null;
                  setConversationFile(nextFile);
                  if (!isImageFile(nextFile)) {
                    setConversationInspectionMode(DEFAULT_INSPECTION_MODE);
                  }
                  if (nextFile) {
                    setSelectedVideoFrame(null);
                    setSelectedTaskId("");
                    setSelectedReferenceTaskId("");
                  }
                }}
                disabled={!session || turnSubmitting || hasActiveTurn}
              />
            </label>
            <InspectionModeField
              value={conversationInspectionMode}
              disabled={!isImageFile(conversationFile) || turnSubmitting || hasActiveTurn}
              disabledReason={selectedVideoFrame
                ? "视频证据帧工具仅使用 YOLO＋VLM 复检。"
                : conversationFile
                  ? "视频仅使用 YOLO＋VLM 复检。"
                  : selectedTaskId
                    ? "关联任务沿用已保存的检查模式，不能在这里改写。"
                    : "选择新图片后可更改检查模式；视频仅使用 YOLO＋VLM 复检。"}
              onChange={setConversationInspectionMode}
            />
            <label className="field">
              <span>关联已有任务（可选）</span>
              <select
                value={selectedTaskId}
                onChange={(event) => {
                  const nextTaskId = event.target.value;
                  const nextTask = tasks.find((task) => task.snapshot.task_id === nextTaskId);
                  pendingTurn.current = null;
                  setSelectedVideoFrame(null);
                  setSelectedTaskId(nextTaskId);
                  setSelectedReferenceTaskId("");
                  setConversationInspectionMode(nextTask
                    ? inspectionModeOf(nextTask)
                    : DEFAULT_INSPECTION_MODE);
                  if (nextTaskId) {
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
            <label className="field">
              <span>对比参考任务（可选）</span>
              <select
                value={selectedReferenceTaskId}
                onChange={(event) => {
                  setSelectedReferenceTaskId(event.target.value);
                  if (event.target.value) {
                    setConversationFile(null);
                    if (conversationFileInput.current) conversationFileInput.current.value = "";
                  }
                }}
                disabled={!session || turnSubmitting || hasActiveTurn || !selectedTaskId || Boolean(selectedVideoFrame)}
              >
                <option value="">不对比</option>
                {tasks.filter((task) => task.snapshot.task_id !== selectedTaskId).map((task) => (
                  <option key={task.snapshot.task_id} value={task.snapshot.task_id}>
                    {task.snapshot.task_id.slice(0, 8)} · {task.instruction}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="form-footer">
            <small>{hasActiveTurn ? "当前消息完成后可继续对话" : `${conversationText.length} / 4000`}</small>
            <Button type="primary" htmlType="submit" disabled={!session || turnSubmitting || hasActiveTurn}>
              {turnSubmitting ? "正在发送…" : pendingMatchesComposer ? "重新发送" : "发送消息"}
            </Button>
          </div>
        </form>
      </section>

          </aside>
        </Layout.Content>
      </Layout>
      <Modal open={intakeOpen} title="新建检查" footer={null} onCancel={() => { if (!submitting) setIntakeOpen(false); }}
        afterOpenChange={(open) => { if (!open && window.matchMedia("(max-width: 1050px)").matches) navigationToggle.current?.focus(); }}
        closable={!submitting} mask={{ closable: !submitting }} keyboard={!submitting} width={560} className="inspection-modal">
      <section className="intake-panel" aria-labelledby="intake-title">
        <div className="section-copy">
          <h2 id="intake-title">提交现场资料</h2>
          <p>支持 JPEG、PNG、MP4、MOV、AVI 与 MKV。文件是否可用以服务器校验为准。</p>
          <p>提交后可在检查详情中启动检查。需要对话规划时，请使用检查助手。</p>
        </div>
        {error ? <div className="conversation-error" role="alert">{error}</div> : null}
        <form ref={form} onSubmit={(event) => void submit(event)}>
          <label className="field">
            <span>现场文件</span>
            <input
              ref={fileInput}
              type="file"
              accept=".jpg,.jpeg,.png,.mp4,.mov,.avi,.mkv,image/jpeg,image/png,video/*"
              onChange={(event) => {
                const nextFile = event.target.files?.[0] ?? null;
                setDirectFile(nextFile);
                if (!isImageFile(nextFile)) setInspectionMode(DEFAULT_INSPECTION_MODE);
              }}
              disabled={!session || submitting}
            />
          </label>
          <InspectionModeField
            value={inspectionMode}
            disabled={!isImageFile(directFile) || submitting}
            disabledReason={directFile
              ? "视频仅使用 YOLO＋VLM 复检。"
              : "选择图片后可更改检查模式；视频仅使用 YOLO＋VLM 复检。"}
            onChange={setInspectionMode}
          />
          <label className="field">
            <span id="inspection-instruction-label">检查要求</span>
            <Input.TextArea
              aria-labelledby="inspection-instruction-label"
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
            <Button type="primary" htmlType="submit" disabled={!session || submitting}>
              {submitting ? "正在提交…" : "提交检查"}
            </Button>
          </div>
        </form>
      </section>

      </Modal>
    </Layout>
  );
}

export default function App(props: AppProps) {
  return (
    <ConfigProvider locale={zhCN} button={{ autoInsertSpace: false }} theme={{
      token: { colorPrimary: "#1677ff", colorText: "#172b4d", colorTextSecondary: "#64748b",
        colorBgLayout: "#f5f6f8", borderRadius: 6, fontSize: 14,
        fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans SC", "Microsoft YaHei", sans-serif' },
    }}>
      <Workbench {...props} />
    </ConfigProvider>
  );
}
