import { AimOutlined, PlusOutlined, ReloadOutlined, SearchOutlined } from "@ant-design/icons";
import { Button, Empty, Input, Spin } from "antd";
import { useMemo, useState } from "react";

import type { SessionSummary } from "./types";

export type ConversationSidebarProps = {
  sessions: SessionSummary[];
  activeSessionId: string;
  onSelectSession(id: string): void;
  onNewConversation(): void;
  loading: boolean;
  disabled?: boolean;
  onRefresh(): void;
};

function formatTimestamp(value: string): string {
  const match = value.match(/^\d{4}-(\d{2})-(\d{2})T(\d{2}):(\d{2})/);
  if (!match) return value;
  return `${match[1]}-${match[2]} ${match[3]}:${match[4]}`;
}

function sessionTitle(session: SessionSummary): string {
  return session.title.trim() || session.session_id;
}

export function ConversationSidebar({
  sessions,
  activeSessionId,
  onSelectSession,
  onNewConversation,
  loading,
  disabled = false,
  onRefresh,
}: ConversationSidebarProps) {
  const [query, setQuery] = useState("");
  const actionsDisabled = disabled || loading;
  const visibleSessions = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase();
    if (!normalizedQuery) return sessions;
    return sessions.filter((session) =>
      sessionTitle(session).toLocaleLowerCase().includes(normalizedQuery)
      || session.session_id.toLocaleLowerCase().includes(normalizedQuery)
    );
  }, [query, sessions]);

  return (
    <aside className="workspace-sidebar" aria-label="对话导航">
      <div className="workspace-brand">
        <span className="workspace-brand__mark" aria-hidden="true"><AimOutlined /></span>
        <span>
          <strong className="workspace-brand__name">管道巡检</strong>
          <span className="workspace-brand__caption">影像检查工作台</span>
        </span>
      </div>

      <Button className="workspace-sidebar__new" type="primary" icon={<PlusOutlined />}
        aria-label="新建对话" block disabled={actionsDisabled} onClick={onNewConversation}>
        新建对话
      </Button>

      <Input className="workspace-search" aria-label="搜索对话" placeholder="搜索对话标题或编号"
        prefix={<SearchOutlined aria-hidden="true" />} value={query} allowClear
        onChange={(event) => setQuery(event.target.value)} />

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
                  <button className={`workspace-task-item${active ? " workspace-task-item--active" : ""}`}
                    type="button" aria-label={`打开对话：${title}`} aria-pressed={active}
                    disabled={actionsDisabled} onClick={() => onSelectSession(session.session_id)}>
                    <span className="workspace-task-item__title" title={title}>{title}</span>
                    <span className="workspace-task-item__meta">
                      <span>{session.turn_count} 条消息 · {session.task_count} 项任务</span>
                      <span aria-hidden="true">·</span>
                      <span>{formatTimestamp(session.updated_at)}</span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        ) : (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={query.trim() ? (
            <span><span>未找到匹配对话</span><small>请调整搜索词</small></span>
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
  );
}
