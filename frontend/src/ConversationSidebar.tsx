import { AimOutlined, MoreOutlined, PlusOutlined, PushpinFilled, ReloadOutlined, SearchOutlined } from "@ant-design/icons";
import { Button, Dropdown, Empty, Input, Modal, Segmented, Spin } from "antd";
import { useMemo, useState } from "react";

import type { SessionSummary, SessionUpdate } from "./types";

export type ConversationSidebarProps = {
  sessions: SessionSummary[];
  activeSessionId: string;
  onSelectSession(id: string): void;
  onNewConversation(): void;
  loading: boolean;
  disabled?: boolean;
  onRefresh(): void;
  onUpdateSession?(sessionId: string, update: SessionUpdate): Promise<void>;
  updatingSessionId?: string;
};

type ConversationSection = "active" | "archived";

function formatTimestamp(value: string): string {
  const match = value.match(/^\d{4}-(\d{2})-(\d{2})T(\d{2}):(\d{2})/);
  if (!match) return value;
  return `${match[1]}-${match[2]} ${match[3]}:${match[4]}`;
}

function sessionTitle(session: SessionSummary): string {
  return session.custom_title?.trim() || session.title.trim() || session.session_id;
}

export function ConversationSidebar({
  sessions,
  activeSessionId,
  onSelectSession,
  onNewConversation,
  loading,
  disabled = false,
  onRefresh,
  onUpdateSession,
  updatingSessionId = "",
}: ConversationSidebarProps) {
  const [query, setQuery] = useState("");
  const [section, setSection] = useState<ConversationSection>("active");
  const [renamingSession, setRenamingSession] = useState<SessionSummary | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [renameError, setRenameError] = useState("");
  const [localUpdatingId, setLocalUpdatingId] = useState("");
  const actionsDisabled = disabled || loading;
  const mutationBusy = Boolean(localUpdatingId || updatingSessionId);
  const activeCount = sessions.filter((session) => !session.archived).length;
  const archivedCount = sessions.length - activeCount;

  const visibleSessions = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase();
    return sessions
      .filter((session) => Boolean(session.archived) === (section === "archived"))
      .filter((session) => !normalizedQuery
        || sessionTitle(session).toLocaleLowerCase().includes(normalizedQuery)
        || session.session_id.toLocaleLowerCase().includes(normalizedQuery))
      .sort((left, right) => {
        const pinnedDifference = Number(Boolean(right.pinned)) - Number(Boolean(left.pinned));
        if (pinnedDifference) return pinnedDifference;
        const dateDifference = right.updated_at.localeCompare(left.updated_at);
        return dateDifference || left.session_id.localeCompare(right.session_id);
      });
  }, [query, section, sessions]);

  async function updateSession(session: SessionSummary, update: SessionUpdate, isRename = false) {
    if (!onUpdateSession || actionsDisabled || mutationBusy) return;
    setLocalUpdatingId(session.session_id);
    if (isRename) setRenameError("");
    try {
      await onUpdateSession(session.session_id, update);
      if (isRename) setRenamingSession(null);
    } catch (error) {
      if (isRename) setRenameError(error instanceof Error ? error.message : "对话名称保存失败");
    } finally {
      setLocalUpdatingId("");
    }
  }

  function beginRename(session: SessionSummary) {
    setRenamingSession(session);
    setRenameValue(sessionTitle(session));
    setRenameError("");
  }

  const trimmedRename = renameValue.trim();
  const renameBusy = Boolean(renamingSession && mutationBusy);

  return (
    <>
      <aside className="workspace-sidebar" aria-label="对话导航">
        <div className="workspace-brand">
          <span className="workspace-brand__mark" aria-hidden="true"><AimOutlined /></span>
          <span>
            <strong className="workspace-brand__name">管道巡检</strong>
            <span className="workspace-brand__caption">影像检查工作台</span>
          </span>
        </div>

        <Button className="workspace-sidebar__new" type="primary" icon={<PlusOutlined />}
          aria-label="新建对话" block disabled={actionsDisabled} onClick={() => {
            setSection("active");
            setQuery("");
            onNewConversation();
          }}>
          新建对话
        </Button>

        <Input className="workspace-search" aria-label="搜索对话" placeholder="搜索对话标题或编号"
          prefix={<SearchOutlined aria-hidden="true" />} value={query} allowClear
          onChange={(event) => setQuery(event.target.value)} />

        <Segmented className="conversation-segments" aria-label="对话分类" block value={section}
          options={[{ label: `对话 ${activeCount}`, value: "active" }, { label: `已归档 ${archivedCount}`, value: "archived" }]}
          onChange={(value) => setSection(value as ConversationSection)} />

        <nav aria-label="对话列表" aria-busy={loading}>
          {loading && sessions.length === 0 ? (
            <div className="workspace-task-list" aria-live="polite">
              <Spin size="small" />
              <span>正在加载对话</span>
            </div>
          ) : visibleSessions.length > 0 ? (
            <ul className="workspace-task-list">
              {visibleSessions.map((session) => {
                const title = sessionTitle(session);
                const active = session.session_id === activeSessionId;
                return (
                  <li key={session.session_id}>
                    <div className="conversation-item-row">
                      <button
                        className={`workspace-task-item conversation-item-row__main${active ? " workspace-task-item--active" : ""}`}
                        type="button" aria-label={`打开对话：${title}`} aria-pressed={active}
                        disabled={actionsDisabled} onClick={() => onSelectSession(session.session_id)}>
                        <span className="workspace-task-item__title" title={title}>{title}</span>
                        {session.pinned && <span className="conversation-item__pinned"><PushpinFilled aria-hidden="true" /> 已置顶</span>}
                        <span className="workspace-task-item__meta">
                          <span>{session.turn_count} 条消息 · {session.task_count} 项任务</span>
                          <span aria-hidden="true">·</span>
                          <span>{formatTimestamp(session.updated_at)}</span>
                        </span>
                      </button>
                      <Dropdown trigger={["click"]} disabled={!onUpdateSession || actionsDisabled || mutationBusy}
                        menu={{
                          items: [
                            { key: "rename", label: "重命名" },
                            { key: "pin", label: session.pinned ? "取消置顶" : "置顶" },
                            { key: "archive", label: session.archived ? "恢复" : "归档" },
                          ],
                          onClick: ({ key }) => {
                            if (key === "rename") beginRename(session);
                            if (key === "pin") void updateSession(session, { pinned: !session.pinned });
                            if (key === "archive") void updateSession(session, { archived: !session.archived });
                          },
                        }}>
                        <Button className="conversation-item__actions" type="text" size="small"
                          icon={<MoreOutlined />} aria-label={`对话操作：${title}`}
                          disabled={!onUpdateSession || actionsDisabled || mutationBusy}
                          onClick={(event) => event.stopPropagation()} />
                      </Dropdown>
                    </div>
                  </li>
                );
              })}
            </ul>
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={query.trim() ? (
              <span><span>未找到匹配对话</span><small>请调整搜索词</small></span>
            ) : section === "archived" ? (
              <span><span>暂无已归档对话</span><small>归档的对话会显示在这里</small></span>
            ) : (
              <span><span>暂无对话</span><small>点击“新建对话”开始检查</small></span>
            )} />
          )}
        </nav>

        <footer className="workspace-sidebar__footer" aria-live="polite">
          <span>{sessions.length} 个对话</span>
          <Button type="text" size="small" icon={<ReloadOutlined />} aria-label="刷新对话列表"
            disabled={actionsDisabled} onClick={onRefresh}>刷新</Button>
        </footer>
      </aside>

      <Modal title="重命名对话" open={Boolean(renamingSession)} okText="保存" cancelText="取消"
        confirmLoading={renameBusy} closable={!renameBusy} mask={{ closable: !renameBusy }}
        okButtonProps={{ "aria-label": "保存", disabled: renameBusy || !trimmedRename || trimmedRename.length > 120 }}
        cancelButtonProps={{ "aria-label": "取消", disabled: renameBusy }}
        onCancel={() => { if (!renameBusy) setRenamingSession(null); }}
        onOk={() => {
          if (renamingSession && trimmedRename && trimmedRename.length <= 120) {
            void updateSession(renamingSession, { title: trimmedRename }, true);
          }
        }}>
        <Input aria-label="对话名称" value={renameValue} maxLength={120} showCount
          status={renameError ? "error" : undefined}
          onChange={(event) => {
            setRenameValue(event.target.value);
            setRenameError("");
          }} />
        {renameError && <div className="conversation-rename-error" role="alert">{renameError}</div>}
      </Modal>
    </>
  );
}
