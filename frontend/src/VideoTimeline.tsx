import { useEffect, useRef, useState } from "react";

import { ApiError, getVideo } from "./api";
import type { VideoDetail, VideoEvent } from "./types";

type VideoTimelineProps = {
  taskId: string;
  snapshotRevision: string;
};

function assetUrl(assetId: string, version: number): string {
  return `/api/assets/${encodeURIComponent(assetId)}/versions/${version}/content`;
}

function eventOrder(left: VideoEvent, right: VideoEvent): number {
  return left.start_seconds - right.start_seconds || left.start_frame - right.start_frame;
}

export function VideoTimeline({ taskId, snapshotRevision }: VideoTimelineProps) {
  const [expanded, setExpanded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [detail, setDetail] = useState<VideoDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadedRevision, setLoadedRevision] = useState<string | null>(null);
  const activeRequest = useRef(0);
  const stateTaskId = useRef(taskId);
  const taskChanged = stateTaskId.current !== taskId;

  useEffect(() => {
    activeRequest.current += 1;
    stateTaskId.current = taskId;
    setExpanded(false);
    setLoading(false);
    setDetail(null);
    setError(null);
    setLoadedRevision(null);
    return () => { activeRequest.current += 1; };
  }, [taskId]);

  async function load(revision: string) {
    const request = ++activeRequest.current;
    setLoading(true);
    setError(null);
    try {
      const next = await getVideo(taskId);
      if (activeRequest.current === request) {
        setDetail(next);
        setLoadedRevision(revision);
      }
    } catch (nextError) {
      if (activeRequest.current === request) {
        setError(nextError instanceof ApiError ? nextError.message : "视频详情加载失败");
      }
    } finally {
      if (activeRequest.current === request) setLoading(false);
    }
  }

  useEffect(() => {
    if (taskChanged || !expanded || loadedRevision === snapshotRevision) return;
    void load(snapshotRevision);
  }, [taskId, snapshotRevision, expanded]);

  const scan = detail?.scan;
  const events = scan
    ? [...scan.state.events, ...(scan.state.finalized ? [] : scan.state.active)].sort(eventOrder)
    : [];
  const frameAssets = new Map(detail?.evidence?.frames.map((frame) => [frame.frame_index, frame.asset]) ?? []);

  return (
    <section className="video-timeline">
      <button type="button" className="secondary-button" aria-expanded={expanded} onClick={() => setExpanded(true)}>
        展开视频候选时间线
      </button>
      {expanded ? (
        <div className="video-timeline__content">
          <h3>视频候选时间线</h3>
          <button type="button" className="secondary-button" disabled={loading} onClick={() => void load(snapshotRevision)}>
            刷新视频证据
          </button>
          <p className="truth-note">以下是自动扫描保存的候选事件，不代表已确认缺陷。</p>
          {loading ? <p className="empty-state">正在加载视频证据…</p> : null}
          {error ? <p className="task-error" role="alert">{error}</p> : null}
          {!loading && detail && !scan ? <p className="truth-note">尚无持久化视频扫描结果。</p> : null}
          {scan ? (
            <>
              <dl className="timeline-summary">
                <div><dt>扫描帧</dt><dd>{scan.state.next_frame}</dd></div>
                <div><dt>质量充分帧</dt><dd>{scan.state.adequate_frames}</dd></div>
                <div><dt>源时间范围</dt><dd>{scan.state.last_timestamp_seconds === null ? "未知" : `0–${scan.state.last_timestamp_seconds} 秒`}</dd></div>
              </dl>
              <p className={scan.decode_complete ? "muted" : "task-error"}>
                {scan.decode_complete ? "解码完整" : `解码未完整${scan.error_code ? `：${scan.error_code}` : ""}`}
              </p>
              {events.length ? (
                <ol className="timeline-events">
                  {events.map((event) => (
                    <li key={event.event_id}>
                      <strong>{event.label}</strong>
                      <span>{event.start_seconds}–{event.end_seconds} 秒</span>
                      <span>帧 {event.start_frame}–{event.end_frame} · {event.observations} 次观测</span>
                      <span className="evidence-links">
                        {event.evidence.map((item) => {
                          const asset = frameAssets.get(item.frame_index);
                          return asset ? (
                            <a key={item.frame_index} href={assetUrl(asset.asset_id, asset.version)} target="_blank" rel="noreferrer">
                              <img
                                src={assetUrl(asset.asset_id, asset.version)}
                                alt=""
                                loading="lazy"
                              />
                              证据帧 {item.frame_index} · {item.timestamp_seconds} 秒
                            </a>
                          ) : null;
                        })}
                      </span>
                    </li>
                  ))}
                </ol>
              ) : <p className="empty-state">没有持久化候选事件。</p>}
              <a href={assetUrl(scan.state.source.asset_id, scan.state.source.version)} target="_blank" rel="noreferrer">打开源视频</a>
            </>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
