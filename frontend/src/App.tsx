import { MenuOutlined } from "@ant-design/icons";
import { Alert, Button, ConfigProvider, Spin } from "antd";
import zhCN from "antd/locale/zh_CN";
import { useCallback, useEffect, useRef, useState } from "react";

import { createSession, listSessions, updateSession } from "./api";
import { ConversationSidebar } from "./ConversationSidebar";
import { SettingsPanel } from "./SettingsPanel";
import InspectionConversation, { type ConversationDraft } from "./InspectionConversation";
import type { SessionRecord, SessionSummary, SessionUpdate } from "./types";

const SESSION_KEY = "pipe-inspector.session-id";

type AppProps = { pollIntervalMs?: number };

function newSummary(session: SessionRecord): SessionSummary {
  return {
    ...session,
    title: "新对话",
    updated_at: session.created_at,
    task_count: 0,
    turn_count: 0,
  };
}

function sortSummaries(items: SessionSummary[]): SessionSummary[] {
  return [...items].sort((left, right) => Number(Boolean(right.pinned)) - Number(Boolean(left.pinned)) ||
    right.updated_at.localeCompare(left.updated_at));
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "无法加载对话列表，请稍后重试";
}

function ConversationShell({ pollIntervalMs }: AppProps) {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [activeSessionId, setActiveSessionId] = useState("");
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const settingsTrigger = useRef<HTMLElement | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [updatingSessionId, setUpdatingSessionId] = useState<string | undefined>();
  const updatingRef = useRef(false);
  const mobileViewport = useRef(window.matchMedia("(max-width: 900px)").matches);
  const [navigationOpen, setNavigationOpen] = useState(() => !mobileViewport.current);
  const draftCache = useRef<Map<string, ConversationDraft>>(new Map());
  const requestSequence = useRef(0);
  const creatingRef = useRef(false);
  const navigationToggle = useRef<HTMLButtonElement>(null);
  const activeSessionRef = useRef("");

  const activate = useCallback((sessionId: string) => {
    activeSessionRef.current = sessionId;
    setActiveSessionId(sessionId);
    localStorage.setItem(SESSION_KEY, sessionId);
    if (mobileViewport.current) setNavigationOpen(false);
  }, []);

  const createAndActivate = useCallback(async () => {
    if (creatingRef.current) return;
    creatingRef.current = true;
    setCreating(true);
    setError(null);
    try {
      const created = await createSession();
      const summary = newSummary(created);
      setSessions((current) => [
        summary,
        ...current.filter((item) => item.session_id !== summary.session_id),
      ]);
      activate(created.session_id);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      creatingRef.current = false;
      setCreating(false);
    }
  }, [activate]);

  const loadSessions = useCallback(async (initial = false) => {
    const sequence = ++requestSequence.current;
    setLoading(true);
    setError(null);
    try {
      const listed = await listSessions();
      if (sequence !== requestSequence.current) return;
      setSessions((current) => {
        const active = current.find(
          (item) => item.session_id === activeSessionRef.current,
        );
        return active && !listed.some((item) => item.session_id === active.session_id)
          ? [active, ...listed]
          : listed;
      });
      if (initial && listed.length === 0) {
        await createAndActivate();
        return;
      }
      if (!activeSessionRef.current) {
        const saved = localStorage.getItem(SESSION_KEY);
        const next = listed.find((item) => item.session_id === saved)?.session_id
          ?? listed[0]?.session_id;
        if (next) activate(next);
      }
    } catch (caught) {
      if (sequence === requestSequence.current) setError(errorMessage(caught));
    } finally {
      if (sequence === requestSequence.current) setLoading(false);
    }
  }, [activate, createAndActivate]);

  useEffect(() => {
    void loadSessions(true);
    return () => { requestSequence.current += 1; };
  }, []); // Initialization is intentionally independent of the active conversation.

  useEffect(() => {
    if (!navigationOpen) return;
    const focusFrame = mobileViewport.current
      ? requestAnimationFrame(() => {
        document.querySelector<HTMLButtonElement>(".workspace-sidebar__new")
          ?.focus({ preventScroll: true });
      })
      : null;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || !mobileViewport.current || event.defaultPrevented) return;
      // Dropdowns and dialogs handle Escape first; keep the drawer underneath open.
      if (document.querySelector('.ant-dropdown:not(.ant-dropdown-hidden), .ant-modal-wrap:not([style*="display: none"]), .application-settings')) return;
      setNavigationOpen(false);
      navigationToggle.current?.focus();
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      if (focusFrame !== null) cancelAnimationFrame(focusFrame);
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [navigationOpen]);

  const updateSummary = useCallback((summary: SessionSummary) => {
    setSessions((current) => {
      const saved = current.find((item) => item.session_id === summary.session_id);
      return sortSummaries([
        { ...summary, custom_title: saved?.custom_title ?? null,
          title: saved?.custom_title ?? summary.title,
          pinned: saved?.pinned ?? false, archived: saved?.archived ?? false,
          updated_at: saved && saved.updated_at > summary.updated_at ? saved.updated_at : summary.updated_at },
        ...current.filter((item) => item.session_id !== summary.session_id),
      ]);
    });
  }, []);

  const changeSession = useCallback(async (sessionId: string, update: SessionUpdate) => {
    if (updatingRef.current) throw new Error("正在保存对话设置，请稍候");
    updatingRef.current = true;
    requestSequence.current += 1;
    setLoading(false);
    setUpdatingSessionId(sessionId);
    setActionError(null);
    try {
      const saved = await updateSession(sessionId, update);
      requestSequence.current += 1;
      setSessions((current) => sortSummaries([
        saved, ...current.filter((item) => item.session_id !== sessionId),
      ]));
    } catch (caught) {
      setActionError(errorMessage(caught));
      throw caught;
    } finally {
      updatingRef.current = false;
      setUpdatingSessionId(undefined);
      setLoading(false);
    }
  }, []);
  const activeSession = sessions.find((item) => item.session_id === activeSessionId);

  const closeNavigation = useCallback(() => {
    setNavigationOpen(false);
    navigationToggle.current?.focus();
  }, []);

  return (
    <div className={`conversation-shell${
      navigationOpen ? " conversation-shell--navigation" : ""
    }`}>
      <Button
        ref={navigationToggle}
        className="conversation-shell__navigation-toggle"
        type="text"
        icon={<MenuOutlined />}
        aria-label={navigationOpen ? "关闭对话导航" : "打开对话导航"}
        aria-expanded={navigationOpen}
        aria-controls="conversation-navigation"
        onClick={() => setNavigationOpen((open) => !open)}
      />
      <div
        id="conversation-navigation"
        className={`conversation-nav${navigationOpen ? " conversation-nav--open" : ""}`}
      >
        <ConversationSidebar
          sessions={sessions}
          activeSessionId={activeSessionId}
          onSelectSession={activate}
          onNewConversation={() => void createAndActivate()}
          loading={loading}
          disabled={creating}
          onUpdateSession={changeSession}
          updatingSessionId={updatingSessionId}
          onRefresh={() => void loadSessions()}
          onOpenSettings={() => {
            settingsTrigger.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
            setSettingsOpen(true);
          }}
        />
        {error && activeSessionId ? (
          <Alert
            type="error"
            showIcon
            message="对话列表加载失败"
            description={error}
            action={<Button size="small" onClick={() => void loadSessions()}>重试</Button>}
          />
        ) : null}
      </div>
      {navigationOpen ? (
        <button
          type="button"
          className="conversation-nav-backdrop"
          aria-label="关闭对话导航"
          onClick={closeNavigation}
        />
      ) : null}
      <main className="conversation-shell__main">
        {activeSessionId ? (
          <InspectionConversation
            key={activeSessionId}
            pollIntervalMs={pollIntervalMs}
            sessionId={activeSessionId}
            onSummary={updateSummary}
            draftCache={draftCache.current}
            notice={<>
              {actionError ? <Alert type="error" showIcon title="对话设置保存失败" description={actionError}
                action={<Button size="small" onClick={() => setActionError(null)}>关闭提示</Button>} /> : null}
              {activeSession?.archived ? <Alert type="info" title="此对话已归档" banner
                action={<Button size="small" disabled={Boolean(updatingSessionId)}
                  onClick={() => void changeSession(activeSessionId, { archived: false }).catch(() => undefined)}>
                  恢复对话</Button>} /> : null}
            </>}
          />
        ) : loading || creating ? (
          <div className="conversation-shell__loading" aria-live="polite">
            <Spin />
            <span>{creating ? "正在新建对话" : "正在加载对话"}</span>
          </div>
        ) : error ? (
          <Alert
            type="error"
            showIcon
            message="无法进入对话"
            description={error}
            action={<Button onClick={() => void loadSessions(true)}>重试</Button>}
          />
        ) : null}
      </main>
      {settingsOpen && <SettingsPanel onClose={() => {
        setSettingsOpen(false);
        requestAnimationFrame(() => settingsTrigger.current?.focus());
      }} />}
    </div>
  );
}

export default function App(props: AppProps) {
  return (
    <ConfigProvider
      locale={zhCN}
      button={{ autoInsertSpace: false }}
      theme={{ token: {
        colorPrimary: "#1677ff",
        colorText: "#172b4d",
        colorTextSecondary: "#64748b",
        colorBgLayout: "#f5f6f8",
        borderRadius: 6,
        fontSize: 14,
        fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans SC", "Microsoft YaHei", sans-serif',
      } }}
    >
      <ConversationShell {...props} />
    </ConfigProvider>
  );
}
