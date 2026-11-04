import { Button, ConfigProvider, Input, Layout, Modal, Spin } from "antd";
import zhCN from "antd/locale/zh_CN";
import { FileSearchOutlined, InfoCircleOutlined, MenuOutlined, MessageOutlined, UploadOutlined } from "@ant-design/icons";
import { ConversationTaskHistory } from "./ConversationTaskHistory";
import { ConversationTaskEntry } from "./ConversationTaskEntry";
import { TaskCard } from "./TaskCard";
import { GuidedRestartAction } from "./GuidedRestartAction";
import { INSPECTION_MODE_TEXT } from "./taskPresentation";
import { defaultImageMode } from "./preferences";
import { useAssistantResize } from "./useAssistantResize";
import { type CSSProperties, type FormEvent, type ReactNode, useEffect, useMemo, useRef, useState } from "react";

import {
  ApiError,
  cancelTask,
  createExampleVideoAsset,
  createSession,
  createTask,
  createTurn,
  getSession,
  getTask,
  getTurn,
  listExampleVideos,
  listTurns,
  startTask,
  uploadAsset,
} from "./api";
import type {
  AssetRef,
  ExampleVideo,
  InspectionMode,
  RuntimeStatus,
  SessionRecord,
  SessionSummary,
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

type DirectSource = "file" | "example";

type PendingSubmission = {
  media: File | string;
  sessionId: string;
  instruction: string;
  requestId: string;
  inputAsset: Promise<AssetRef>;
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
  sessionId?: string;
  onSummary?: (summary: SessionSummary) => void;
  draftCache?: Map<string, ConversationDraft>;
  onToggleNavigation?: () => void;
  navigationOpen?: boolean;
  initialDetailsOpen?: boolean;
  notice?: ReactNode;
};

export type ConversationDraft = {
  text: string;
  file: File | null;
  mode: InspectionMode;
  taskId: string;
  follow: boolean;
  referenceId: string;
  frame: SelectedVideoFrame | null;
  focusedId: string;
  waitingGuidedId: string;
  detailsOpen: boolean;
  pendingTurn: { current: PendingTurn | null };
  pendingSubmission: { current: PendingSubmission | null };
  followCreatedTurn: { current: string | null };
  directFile: File | null;
  directSource: DirectSource;
  directExample: ExampleVideo | null;
  instruction: string;
  inspectionMode: InspectionMode;
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
  createdTask?: TaskRecord;
  resending: boolean;
  onResend: (turn: TurnRecord) => void;
  onViewTask: (id: string) => void;
};

function TurnCard({ turn, sessionId, linkedTask, createdTask, resending, onResend, onViewTask }: TurnCardProps) {
  return (
    <article className="turn-card">
      <div className="turn-message turn-message--user">
        <span>你</span>
        <p>{turn.text}</p>
        {turn.input_asset && !sameAssetRef(turn.input_asset, createdTask?.input_asset ?? null) ? (
          <a href={evidenceUrl(turn.input_asset)} target="_blank" rel="noreferrer">打开本轮输入文件</a>
        ) : null}
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
        {createdTask && <ConversationTaskEntry task={createdTask} turnText={turn.text} onViewTask={onViewTask} />}
        {linkedTask && linkedTask.snapshot.task_id !== createdTask?.snapshot.task_id ? <Button size="small" className="turn-task-link" onClick={() => onViewTask(linkedTask.snapshot.task_id)}>查看任务与证据 · {linkedTask.snapshot.task_id.slice(0, 8)}</Button> : null}
        {turn.status === "completed" ? (
          <details className="turn-extra" open={Boolean(turn.reference_task_id)}><summary>结果对比</summary><ComparisonPanel sessionId={sessionId} requestId={turn.request_id} /></details>
        ) : null}
      </div>
    </article>
  );
}

function Workbench({ pollIntervalMs = 2_000, sessionId, onSummary, draftCache,
  onToggleNavigation, navigationOpen, initialDetailsOpen = false, notice }: AppProps) {
  const cached = useRef(sessionId ? draftCache?.get(sessionId) : undefined).current;
  const [focusedTaskId, setFocusedTaskId] = useState(cached?.focusedId ?? "");
  const [intakeOpen, setIntakeOpen] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(cached?.detailsOpen ?? initialDetailsOpen);
  const [optionsOpen, setOptionsOpen] = useState(false);
  const [session, setSession] = useState<SessionRecord | null>(null);
  const [tasks, setTasks] = useState<TaskRecord[]>([]);
  const [turns, setTurns] = useState<TurnRecord[]>([]);
  const [instruction, setInstruction] = useState(cached?.instruction ?? "");
  const [directFile, setDirectFile] = useState<File | null>(cached?.directFile ?? null);
  const [directSource, setDirectSource] = useState<DirectSource>(cached?.directSource ?? "file");
  const [directExample, setDirectExample] = useState<ExampleVideo | null>(cached?.directExample ?? null);
  const [examples, setExamples] = useState<ExampleVideo[] | null>(null);
  const [examplesError, setExamplesError] = useState<string | null>(null);
  const [catalogAttempt, setCatalogAttempt] = useState(0);
  const [inspectionMode, setInspectionMode] = useState<InspectionMode>(cached?.inspectionMode ?? defaultImageMode());
  const [conversationText, setConversationText] = useState(cached?.text ?? "");
  const [conversationFile, setConversationFile] = useState<File | null>(cached?.file ?? null);
  const [conversationInspectionMode, setConversationInspectionMode] =
    useState<InspectionMode>(cached?.mode ?? defaultImageMode());
  const [selectedTaskId, setSelectedTaskId] = useState(cached?.taskId ?? "");
  const [followFocusedTask, setFollowFocusedTask] = useState(cached?.follow ?? true);
  const [restartingGuided, setRestartingGuided] = useState(false);
  const [waitingGuidedTaskId, setWaitingGuidedTaskId] = useState(cached?.waitingGuidedId ?? "");
  const [selectedReferenceTaskId, setSelectedReferenceTaskId] = useState(cached?.referenceId ?? "");
  const [selectedVideoFrame, setSelectedVideoFrame] = useState<SelectedVideoFrame | null>(cached?.frame ?? null);
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
  const newPendingSubmission = useRef<PendingSubmission | null>(null);
  const newPendingTurn = useRef<PendingTurn | null>(null);
  const pendingSubmission = cached?.pendingSubmission ?? newPendingSubmission;
  const pendingTurn = cached?.pendingTurn ?? newPendingTurn;
  const navigationToggle = useRef<HTMLButtonElement>(null);
  const workspaceContent = useRef<HTMLElement>(null);
  const { width: assistantWidth, separatorProps } = useAssistantResize(workspaceContent, {
    storageKey: "pipe-inspector.detail-width", defaultWidth: 520,
  });
  const transcript = useRef<HTMLDivElement>(null);
  const previousTimelineLength = useRef<number | null>(null);
  const newFollowCreatedTurn = useRef<string | null>(null);
  const followCreatedTurn = cached?.followCreatedTurn ?? newFollowCreatedTurn;
  const focusedTask = tasks.find((task) => task.snapshot.task_id === focusedTaskId) ?? tasks[0];
  const contextTask = conversationFile ? undefined : tasks.find((task) => task.snapshot.task_id === (
    selectedVideoFrame?.parentTaskId ?? (followFocusedTask ? focusedTask?.snapshot.task_id : selectedTaskId)
  ));
  const effectiveTaskId = contextTask?.snapshot.task_id ?? "";
  const effectiveMode = selectedVideoFrame ? DEFAULT_INSPECTION_MODE
    : contextTask ? inspectionModeOf(contextTask) : conversationInspectionMode;
  const waitingForGuided = Boolean(waitingGuidedTaskId && effectiveTaskId === waitingGuidedTaskId &&
    contextTask && isActiveTask(contextTask));

  const timeline = useMemo(() => {
    const byRequest = new Map(tasks.map((task) => [task.request_id, task]));
    const byId = new Map(tasks.map((task) => [task.snapshot.task_id, task]));
    const entries = turns.map((turn) => {
      // Tasks admitted by a turn reuse that turn’s request ID. Later questions can link
      // to the same task without owning it or changing its position in the history.
      const createdTask = byRequest.get(turn.request_id);
      byRequest.delete(turn.request_id);
      return { kind: "turn" as const, turn, createdTask, created: turn.created_at,
        linkedTask: turn.linked_task_id ? byId.get(turn.linked_task_id) : undefined };
    });
    const standalone = [...byRequest.values()].map((task) => ({
      kind: "task" as const, task, created: task.created_at,
    }));
    return [...entries, ...standalone].sort((a, b) => Date.parse(a.created) - Date.parse(b.created));
  }, [tasks, turns]);

  function showTaskEvidence(id: string) {
    followCreatedTurn.current = null;
    setFocusedTaskId(id);
    setDetailsOpen(true);
  }

  function discussFocusedTask() {
    followCreatedTurn.current = null;
    setFollowFocusedTask(true);
    setSelectedTaskId("");
    setSelectedReferenceTaskId("");
    setSelectedVideoFrame(null);
    setConversationFile(null);
    if (conversationFileInput.current) conversationFileInput.current.value = "";
    pendingTurn.current = null;
  }

  useEffect(() => {
    const requestId = followCreatedTurn.current;
    if (!requestId) return;
    const turn = turns.find((item) => item.request_id === requestId);
    const linkedId = turn?.linked_task_id;
    if (linkedId && linkedId === turn?.task_id) {
      followCreatedTurn.current = null;
      return;
    }
    if (linkedId && tasks.some((task) => task.snapshot.task_id === linkedId)) {
      followCreatedTurn.current = null;
      setFocusedTaskId(linkedId);
      setFollowFocusedTask(true);
      setSelectedTaskId("");
    }
  }, [tasks, turns]);

  useEffect(() => {
    const id = sessionId ?? session?.session_id;
    if (id && draftCache) draftCache.set(id, {
      text: conversationText, file: conversationFile, mode: conversationInspectionMode,
      taskId: selectedTaskId, follow: followFocusedTask, referenceId: selectedReferenceTaskId,
      frame: selectedVideoFrame, focusedId: focusedTaskId, waitingGuidedId: waitingGuidedTaskId,
      detailsOpen, pendingTurn, pendingSubmission, followCreatedTurn, directFile, directSource,
      directExample, instruction, inspectionMode,
    });
  });

  useEffect(() => {
    if (!intakeOpen || directSource !== "example" || examples !== null) return;
    let active = true;
    setExamplesError(null);
    listExampleVideos()
      .then((catalog) => { if (active) setExamples(catalog); })
      .catch((reason) => { if (active) setExamplesError(formatError(reason)); });
    return () => { active = false; };
  }, [intakeOpen, directSource, examples, catalogAttempt]);

  useEffect(() => {
    if (!session || !onSummary) return;
    const updated = [session.created_at, ...turns.map((turn) => turn.updated_at),
      ...tasks.map((task) => task.snapshot.updated_at)].sort().at(-1)!;
    onSummary({ ...session, title: (turns[0]?.text ?? tasks[0]?.instruction ?? "新对话").slice(0, 120),
      updated_at: updated, task_count: tasks.length, turn_count: turns.length });
  }, [session, tasks, turns, onSummary]);

  useEffect(() => {
    if (loading) {
      previousTimelineLength.current = null;
      return;
    }
    const previous = previousTimelineLength.current;
    previousTimelineLength.current = timeline.length;
    if (transcript.current && (previous === null ? turns.length > 0 : timeline.length > previous)) {
      transcript.current.scrollTop = transcript.current.scrollHeight;
    }
  }, [loading, timeline.length, turns.length]);

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
      setFollowFocusedTask(true);
      setWaitingGuidedTaskId("");
      followCreatedTurn.current = null;
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
    const savedSessionId = sessionId ?? localStorage.getItem(SESSION_KEY);
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
        if (!cached?.focusedId) {
          const recent = [...detail.turns].reverse().find((turn) =>
            turn.linked_task_id && detail.tasks.some((task) => task.snapshot.task_id === turn.linked_task_id),
          );
          if (recent?.linked_task_id) setFocusedTaskId(recent.linked_task_id);
        }
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
  }, [sessionId]);

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
    const media = directSource === "example" ? directExample?.id : directFile;
    const mode = directSource === "example" ? DEFAULT_INSPECTION_MODE : inspectionMode;
    const cleanInstruction = instruction.trim();
    if (!media) {
      setError(directSource === "example" ? "请选择项目示例视频" : "请选择现场图片或视频");
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
        pending.media !== media ||
        pending.sessionId !== session.session_id ||
        pending.instruction !== cleanInstruction ||
        pending.inspectionMode !== mode
      ) {
        const reusableAsset = pending?.media === media && pending.sessionId === session.session_id
          ? pending.inputAsset : null;
        const inputAsset = reusableAsset ?? (typeof media === "string"
          ? createExampleVideoAsset(media) : uploadAsset(media)).then((asset) => asset.ref);
        pending = {
          media,
          sessionId: session.session_id,
          instruction: cleanInstruction,
          requestId: crypto.randomUUID(),
          inputAsset,
          inspectionMode: mode,
        };
        pendingSubmission.current = pending;
      }
      let inputAsset: AssetRef;
      try {
        inputAsset = await pending.inputAsset;
      } catch (nextError) {
        if (pendingSubmission.current?.inputAsset === pending.inputAsset) pendingSubmission.current = null;
        throw nextError;
      }
      const created = await createTask({
        request_id: pending.requestId,
        session_id: session.session_id,
        input_asset: inputAsset,
        instruction: cleanInstruction,
        inspection_mode: pending.inspectionMode,
      });
      const savedDraft = draftCache?.get(session.session_id);
      const draftMedia = savedDraft?.directSource === "example" ? savedDraft.directExample?.id : savedDraft?.directFile;
      if (savedDraft?.pendingSubmission.current?.requestId === pending.requestId &&
          savedDraft.instruction.trim() === pending.instruction && draftMedia === pending.media) {
        savedDraft.instruction = "";
        savedDraft.directFile = null;
        savedDraft.directSource = "file";
        savedDraft.directExample = null;
        savedDraft.inspectionMode = DEFAULT_INSPECTION_MODE;
        savedDraft.focusedId = created.snapshot.task_id;
      }
      if (pendingSubmission.current?.requestId === pending.requestId) pendingSubmission.current = null;
      setTasks((current) => upsertTask(current, created));
      setFocusedTaskId(created.snapshot.task_id);
      if (followFocusedTask) setSelectedReferenceTaskId("");
      setIntakeOpen(false);
      setInstruction("");
      setDirectFile(null);
      setDirectSource("file");
      setDirectExample(null);
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
      const savedDraft = draftCache?.get(session.session_id);
      const sameDraft = !savedDraft || (savedDraft.text.trim() === submission.text &&
        savedDraft.referenceId === (submission.reference_task_id ?? ""));
      if (sameDraft && !submission.reference_task_id &&
          pendingTurn.current?.request_id === submission.request_id) {
        followCreatedTurn.current = created.request_id;
      }
      if (sameDraft && savedDraft?.pendingTurn.current?.request_id === submission.request_id) {
        savedDraft.text = "";
        savedDraft.file = null;
        savedDraft.frame = null;
        savedDraft.referenceId = "";
      }
      if (pendingTurn.current?.request_id === submission.request_id) pendingTurn.current = null;
      setConversationText("");
      setConversationFile(null);
      setConversationInspectionMode(DEFAULT_INSPECTION_MODE);
      setSelectedReferenceTaskId("");
      setSelectedVideoFrame(null);
      if (conversationFileInput.current) conversationFileInput.current.value = "";
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
    if (!session || activeTurnIds.length > 0 || restartingGuided || waitingForGuided) return;
    const cleanText = conversationText.trim();
    if (!cleanText) {
      setConversationError("请输入对话内容");
      return;
    }
    const taskId = selectedVideoFrame?.parentTaskId ?? (effectiveTaskId || null);
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
      inspectionModeOf(pending) === effectiveMode &&
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
          inspection_mode: effectiveMode,
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
  const composerBusy = turnSubmitting || hasActiveTurn || restartingGuided;
  const pendingMatchesComposer = Boolean(
    pendingTurn.current &&
    pendingTurn.current.sessionId === session?.session_id &&
    pendingTurn.current.text === conversationText.trim() &&
    pendingTurn.current.task_id === (effectiveTaskId || null) &&
    pendingTurn.current.reference_task_id === (selectedReferenceTaskId || null) &&
    inspectionModeOf(pendingTurn.current) === effectiveMode &&
    pendingTurn.current.file === conversationFile &&
    sameAssetRef(pendingTurn.current.frameAsset, selectedVideoFrame?.frame.asset ?? null),
  );

  function selectVideoFrame(parentTaskId: string, frame: VideoEvidenceFrame) {
    setOptionsOpen(true);
    setSelectedVideoFrame({ parentTaskId, frame });
    setSelectedTaskId(parentTaskId);
    setFollowFocusedTask(false);
    setSelectedReferenceTaskId("");
    setConversationFile(null);
    setConversationInspectionMode(DEFAULT_INSPECTION_MODE);
    if (conversationFileInput.current) conversationFileInput.current.value = "";
    pendingTurn.current = null;
  }

  return (
    <Layout className="conversation-workbench">
      <Layout.Header className="workspace-header">
        <div className="workspace-header__title">
          {onToggleNavigation ? <Button ref={navigationToggle} icon={<MenuOutlined />} type="text"
            aria-label={navigationOpen ? "收起对话导航" : "展开对话导航"} onClick={onToggleNavigation} /> : null}
          <h1>管道影像检查</h1><span className="workspace-header__caption">独立对话</span>
        </div>
        <div className="workspace-header__actions">
          {session ? <span className="workspace-session" title={session.session_id}><code>{session.session_id}</code></span> : null}
          {session ? <details className="conversation-report-menu"><summary>统一报告</summary><div>
            <a href={`/api/sessions/${session.session_id}/report?format=html`} target="_blank" rel="noreferrer">HTML 统一报告</a>
            <a href={`/api/sessions/${session.session_id}/report?format=json`} target="_blank" rel="noreferrer">JSON 统一报告</a>
          </div></details> : null}
          <Button onClick={() => setIntakeOpen(true)}>新建检查</Button>
          <Button aria-label={detailsOpen ? "收起任务详情" : "展开任务详情"}
            onClick={() => setDetailsOpen((value) => !value)}>{detailsOpen ? "收起详情" : `任务与证据 ${tasks.length}`}</Button>
        </div>
      </Layout.Header>
      {notice}
      {error && !intakeOpen ? <div className="workspace-error" role="alert"><InfoCircleOutlined />{error}
        {!session && !loading && !sessionId ? <Button onClick={() => void startNewSession()}>新建会话</Button> : null}
      </div> : null}
      <Layout.Content ref={workspaceContent} style={{ "--detail-width": `${assistantWidth}px` } as CSSProperties}
        className={`conversation-content${detailsOpen ? " conversation-content--details" : ""}`}>
        <main className="conversation-main-surface">
      <section className="conversation-panel" aria-labelledby="conversation-title">
        <div className="conversation-thread-heading"><h2 id="conversation-title">检查对话</h2><span>{turns.length} 条消息 · {tasks.length} 项任务</span></div>
        <div ref={transcript} className="transcript" aria-live="polite" aria-label="对话消息">
          {!loading && timeline.length === 0 ? <div className="assistant-empty"><FileSearchOutlined aria-hidden="true" /><h3>从一份现场影像开始</h3><p>在同一个对话中检查原图、继续调用工具，并查看统一报告。</p><Button type="primary" icon={<UploadOutlined />} onClick={() => { setOptionsOpen(true); conversationFileInput.current?.click(); }}>上传现场影像</Button><p className="assistant-empty__formats">JPEG、PNG 或视频 · 同图的任务与证据保留在当前对话</p></div> : null}
          {timeline.map((item) => item.kind === "turn" ? (
            <TurnCard
              key={item.turn.request_id}
              turn={item.turn}
              sessionId={session?.session_id ?? item.turn.session_id}
              linkedTask={item.linkedTask}
              createdTask={item.createdTask}
              resending={turnSubmitting}
              onResend={(nextTurn) => void postTurn(nextTurn)}
              onViewTask={showTaskEvidence}
            />
          ) : (
            <ConversationTaskEntry key={item.task.snapshot.task_id} task={item.task} onViewTask={showTaskEvidence} />
          ))}
        </div>
        {conversationError ? <div className="conversation-error" role="alert">{conversationError}</div> : null}
        <form ref={conversationForm} className="conversation-form" onSubmit={(event) => void submitTurn(event)}>
          <details className="assistant-context" aria-label="当前对话上下文">
            <summary>{conversationFile ? `新附件：${conversationFile.name}` : contextTask ? `当前影像 · ${contextTask.snapshot.task_id.slice(0, 8)} · ${INSPECTION_MODE_TEXT[effectiveMode]}` : "会话上下文 · 上传影像开始检查"}</summary>
            {contextTask ? <><p title={contextTask.instruction}>{contextTask.instruction}</p><small>{followFocusedTask ? "跟随当前查看的任务" : "已固定当前对话目标"}</small></> : null}
            {focusedTask && (effectiveTaskId !== focusedTask.snapshot.task_id || !followFocusedTask) ? (
              <Button type="link" size="small" disabled={composerBusy} onClick={discussFocusedTask}>讨论当前查看的任务</Button>
            ) : null}
          </details>
          <GuidedRestartAction compact task={contextTask ?? null} disabled={composerBusy || Boolean(selectedVideoFrame)}
            onBusyChange={(busy) => {
              if (busy && contextTask) {
                setSelectedTaskId(contextTask.snapshot.task_id);
                setFollowFocusedTask(false);
                followCreatedTurn.current = null;
              }
              setRestartingGuided(busy);
            }}
            onAdmitted={(task) => setTasks((current) => upsertTask(current, task))}
            onStarted={(task) => {
              const savedDraft = draftCache?.get(task.session_id);
              if (savedDraft) {
                savedDraft.focusedId = task.snapshot.task_id;
                savedDraft.taskId = task.snapshot.task_id;
                savedDraft.follow = false;
                savedDraft.referenceId = "";
                savedDraft.waitingGuidedId = task.snapshot.task_id;
              }
              setTasks((current) => upsertTask(current, task));
              setFocusedTaskId(task.snapshot.task_id);
              setSelectedTaskId(task.snapshot.task_id);
              setFollowFocusedTask(false);
              setSelectedReferenceTaskId("");
              setWaitingGuidedTaskId(task.snapshot.task_id);
              pendingTurn.current = null;
            }} />
          {waitingForGuided ? <p className="assistant-baseline-note" role="status">原图复检任务尚未完成，当前草稿已保留。待任务已完成或部分完成后，可继续发送工具请求；若等待人工复核，请先查看证据并复核。</p> : null}
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
              onChange={(event) => {
                followCreatedTurn.current = null;
                setConversationText(event.target.value);
                if (followFocusedTask && contextTask) {
                  setSelectedTaskId(contextTask.snapshot.task_id);
                  setFollowFocusedTask(false);
                }
              }}
              maxLength={4_000}
              autoSize={{ minRows: 2, maxRows: 6 }}
              placeholder="例如：请检查这份影像，或解释上一项检查结果"
              disabled={!session || composerBusy}
            />
          </label>
          <details className="composer-options" open={optionsOpen} onToggle={(event) => setOptionsOpen(event.currentTarget.open)}>
            <summary>附件与检查选项{conversationFile ? ` · ${conversationFile.name}` : ""}</summary>
            <p className="conversation-note">对新图或关联原图，可以提出“必要时按需增强后再检查”；每次工具请求保留独立的执行记录。</p>
          <div className="conversation-options">
            <label className="field">
              <span>对话附件（可选）</span>
              <input
                ref={conversationFileInput}
                type="file"
                accept=".jpg,.jpeg,.png,.mp4,.mov,.avi,.mkv,image/jpeg,image/png,video/*"
                onChange={(event) => {
                  const nextFile = event.target.files?.[0] ?? null;
                  followCreatedTurn.current = null;
                  pendingTurn.current = null;
                  setConversationFile(nextFile);
                  setConversationInspectionMode(isImageFile(nextFile) ? defaultImageMode() : DEFAULT_INSPECTION_MODE);
                  if (nextFile) {
                    setSelectedVideoFrame(null);
                    setSelectedTaskId("");
                    setFollowFocusedTask(false);
                    setSelectedReferenceTaskId("");
                  }
                }}
                disabled={!session || composerBusy}
              />
            </label>
            <InspectionModeField
              value={effectiveMode}
              disabled={!isImageFile(conversationFile) || composerBusy}
              disabledReason={selectedVideoFrame
                ? "视频证据帧工具仅使用 YOLO＋VLM 复检。"
                : conversationFile
                  ? "视频仅使用 YOLO＋VLM 复检。"
                  : effectiveTaskId
                    ? "关联任务沿用已保存的检查模式，不能在这里改写。"
                    : "选择新图片后可更改检查模式；视频仅使用 YOLO＋VLM 复检。"}
              onChange={setConversationInspectionMode}
            />
            <div className="assistant-target-options">
            <label className="field">
              <span>关联已有任务（可选）</span>
              <select
                value={effectiveTaskId}
                onChange={(event) => {
                  const nextTaskId = event.target.value;
                  const nextTask = tasks.find((task) => task.snapshot.task_id === nextTaskId);
                  pendingTurn.current = null;
                  setSelectedVideoFrame(null);
                  setSelectedTaskId(nextTaskId);
                  setFollowFocusedTask(false);
                  followCreatedTurn.current = null;
                  setSelectedReferenceTaskId("");
                  setConversationInspectionMode(nextTask
                    ? inspectionModeOf(nextTask)
                    : DEFAULT_INSPECTION_MODE);
                  if (nextTaskId) {
                    setConversationFile(null);
                    if (conversationFileInput.current) conversationFileInput.current.value = "";
                  }
                }}
                disabled={!session || composerBusy}
              >
                <option value="">会话对话（不指定任务）</option>
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
                  setSelectedTaskId(effectiveTaskId);
                  setFollowFocusedTask(false);
                  if (event.target.value) {
                    setConversationFile(null);
                    if (conversationFileInput.current) conversationFileInput.current.value = "";
                  }
                }}
                disabled={!session || composerBusy || !effectiveTaskId || Boolean(selectedVideoFrame)}
              >
                <option value="">不对比</option>
                {tasks.filter((task) => task.snapshot.task_id !== effectiveTaskId).map((task) => (
                  <option key={task.snapshot.task_id} value={task.snapshot.task_id}>
                    {task.snapshot.task_id.slice(0, 8)} · {task.instruction}
                  </option>
                ))}
              </select>
            </label>
            </div>
          </div>
          </details>
          <div className="form-footer">
            <small>{hasActiveTurn ? "当前消息完成后可继续对话" : `${conversationText.length} / 4000`}</small>
            <Button type="primary" htmlType="submit" disabled={!session || composerBusy || waitingForGuided}>
              {turnSubmitting ? "正在发送…" : pendingMatchesComposer ? "重新发送" : "发送消息"}
            </Button>
          </div>
        </form>
      </section>
        </main>
        {detailsOpen ? <div {...separatorProps} className="assistant-resizer" aria-label="调整任务详情宽度" title="拖动调整任务详情宽度" /> : null}
        <aside className="inspection-workspace" hidden={!detailsOpen} aria-label="任务与证据">
          <div className="inspection-panel-heading"><strong>当前对话的任务与证据</strong><Button size="small" aria-label="关闭任务详情" onClick={() => setDetailsOpen(false)}>关闭</Button></div>
          <details className="conversation-task-list" open><summary>影像与执行记录 · {tasks.length}</summary>
            <ConversationTaskHistory tasks={tasks} activeTaskId={focusedTask?.snapshot.task_id ?? ""}
              onSelectTask={(id) => { followCreatedTurn.current = null; setFocusedTaskId(id); }} />
          </details>
          <section className="inspection-detail" aria-label="检查详情">
            {loading ? <div className="workspace-loading"><Spin /><p>正在恢复对话</p></div> : focusedTask ? (
              <TaskCard key={focusedTask.snapshot.task_id} task={focusedTask}
                onStart={startInspection} onCancel={cancelInspection}
                starting={startingTaskIds.has(focusedTask.snapshot.task_id)}
                cancelling={cancellingTaskIds.has(focusedTask.snapshot.task_id)}
                onReviewed={(updated, snapshot) => setTasks((current) => upsertTask(current, { ...updated, snapshot }))}
                onSelectFrame={selectVideoFrame} frameSelectionDisabled={composerBusy} />
            ) : <p className="empty-state">上传影像后，检查与工具执行记录会显示在这里。</p>}
          </section>
        </aside>
      </Layout.Content>
      <Modal open={intakeOpen} title="新建检查" footer={null} onCancel={() => { if (!submitting) setIntakeOpen(false); }}
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
            <span>素材来源</span>
            <select value={directSource} disabled={!session || submitting} onChange={(event) => {
              const source = event.target.value as DirectSource;
              setDirectSource(source);
              setError(null);
              if (source === "example" && !instruction.trim()) setInstruction(directExample?.instruction ?? "");
              if (source === "file" && instruction === directExample?.instruction) setInstruction("");
            }}>
              <option value="file">上传现场文件</option>
              <option value="example">项目示例</option>
            </select>
          </label>
          <div hidden={directSource !== "file"}><label className="field">
            <span>现场文件</span>
            <input
              ref={fileInput}
              type="file"
              accept=".jpg,.jpeg,.png,.mp4,.mov,.avi,.mkv,image/jpeg,image/png,video/*"
              onChange={(event) => {
                const nextFile = event.target.files?.[0] ?? null;
                setDirectFile(nextFile);
                setInspectionMode(isImageFile(nextFile) ? defaultImageMode() : DEFAULT_INSPECTION_MODE);
              }}
              disabled={!session || submitting}
            />
          </label></div>
          {directSource === "example" ? <>
            <label className="field">
              <span>项目示例视频</span>
              <select value={directExample?.id ?? ""} disabled={!session || submitting || !examples?.length}
                onChange={(event) => {
                  const next = examples?.find((item) => item.id === event.target.value) ?? null;
                  if (!instruction.trim() || instruction === directExample?.instruction) {
                    setInstruction(next?.instruction ?? "");
                  }
                  setDirectExample(next);
                  setError(null);
                }}>
                <option value="">{examplesError ? "项目示例读取失败" : examples === null ? "正在读取项目示例…" :
                  examples.length ? "请选择示例视频" : "暂无项目示例"}</option>
                {examples?.map((example) => <option key={example.id} value={example.id}>{example.title}</option>)}
              </select>
            </label>
            <small>直接使用项目中的视频，无需上传。可在下方编辑检查要求。</small>
            {examplesError ? <div className="conversation-error" role="alert">
              {examplesError} <Button size="small" onClick={() => setCatalogAttempt((value) => value + 1)}>重试读取示例</Button>
            </div> : null}
          </> : null}
          <InspectionModeField
            value={directSource === "example" ? DEFAULT_INSPECTION_MODE : inspectionMode}
            disabled={directSource === "example" || !isImageFile(directFile) || submitting}
            disabledReason={directSource === "example" || directFile
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
            <Button type="primary" htmlType="submit" disabled={!session || submitting ||
              (directSource === "example" && !examples?.some((item) => item.id === directExample?.id))}>
              {submitting ? "正在提交…" : "提交检查"}
            </Button>
          </div>
        </form>
      </section>

      </Modal>
    </Layout>
  );
}

export default function InspectionConversation(props: AppProps) {
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
