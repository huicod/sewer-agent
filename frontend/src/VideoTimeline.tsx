import { useEffect, useRef, useState } from "react";

import { ApiError, getVideo } from "./api";
import { VideoProcessingTimeline } from "./VideoProcessingTimeline";
import type {
  AssetRef,
  VideoDetail,
  VideoEvent,
  VideoEvidenceFrame,
  VideoSceneAssessment,
} from "./types";

const SCENE_LABELS: Record<VideoSceneAssessment["scene"], string> = {
  pipe: "管内画面", outside: "井外画面", unassessable: "无法观察", unknown: "场景不确定",
};

type VideoTimelineProps = {
  taskId: string;
  snapshotRevision: string;
  onSelectFrame?: (frame: VideoEvidenceFrame) => void;
  frameSelectionDisabled?: boolean;
  visible?: boolean;
  sourceAsset?: AssetRef;
  durationSeconds?: number | null;
  currentTime?: number;
  processedFrames?: number;
  onSeek?: (seconds: number) => void;
};

function assetUrl(assetId: string, version: number): string {
  return `/api/assets/${encodeURIComponent(assetId)}/versions/${version}/content`;
}

function eventOrder(left: VideoEvent, right: VideoEvent): number {
  return left.start_seconds - right.start_seconds || left.start_frame - right.start_frame;
}

export function VideoTimeline({
  taskId,
  snapshotRevision,
  onSelectFrame,
  frameSelectionDisabled = false,
  visible = false,
  sourceAsset,
  durationSeconds = null,
  currentTime = 0,
  processedFrames = 0,
  onSeek,
}: VideoTimelineProps) {
  const [expanded, setExpanded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [detail, setDetail] = useState<VideoDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadedRevision, setLoadedRevision] = useState<string | null>(null);
  const activeRequest = useRef(0);
  const identity = `${taskId}:${sourceAsset?.asset_id ?? ""}:${sourceAsset?.version ?? ""}`;
  const stateIdentity = useRef(identity);
  const taskChanged = stateIdentity.current !== identity;

  useEffect(() => {
    activeRequest.current += 1;
    stateIdentity.current = identity;
    setExpanded(false);
    setLoading(false);
    setDetail(null);
    setError(null);
    setLoadedRevision(null);
    return () => { activeRequest.current += 1; };
  }, [identity]);

  async function load(revision: string) {
    const request = ++activeRequest.current;
    setLoading(true);
    setError(null);
    try {
      const next = await getVideo(taskId, sourceAsset);
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
    if ((!visible && !expanded) || (!taskChanged && loadedRevision === snapshotRevision)) return;
    void load(snapshotRevision);
  }, [taskId, snapshotRevision, expanded, visible, sourceAsset?.asset_id, sourceAsset?.version]);

  const currentDetail = taskChanged ? null : detail;
  const scan = currentDetail?.scan;
  const state = scan?.state ?? currentDetail?.scan_progress ?? null;
  const selection = currentDetail?.evidence?.selection;
  const events = state
    ? [...state.events, ...(state.finalized ? [] : state.active)].sort(eventOrder)
    : [];
  const frameAssets = new Map(currentDetail?.evidence?.frames.map((frame) => [frame.frame_index, frame.asset]) ?? []);
  const storedFrames = [...(currentDetail?.evidence?.frames ?? [])].sort((left, right) =>
    left.frame_index - right.frame_index || left.timestamp_seconds - right.timestamp_seconds,
  );
  const observedEnd = storedFrames.reduce((end, frame) => Math.max(end, frame.timestamp_seconds),
    events.reduce((end, event) => Math.max(end, event.end_seconds), state?.last_timestamp_seconds ?? 0));
  const seekUnavailableReason = currentDetail?.scan_start_seconds == null
    ? "扫描时间起点尚未保存，可先使用播放器浏览。"
    : currentDetail.scan_start_seconds >= 0.001
      ? "此视频保留了原始时间偏移，请使用播放器定位；证据帧仍可直接查看。"
      : durationSeconds !== null && observedEnd > durationSeconds + 0.01
        ? "已保存时间戳超出播放器时长，请使用播放器定位；证据帧仍可直接查看。"
        : undefined;

  return (
    <section className="video-timeline">
      {visible ? <>
        <VideoProcessingTimeline key={`${taskId}:${sourceAsset?.asset_id}:${sourceAsset?.version}`}
          state={state} frames={storedFrames} reviewFrames={currentDetail?.evidence?.review_frames ?? []}
          scanStartSeconds={currentDetail?.scan_start_seconds ?? null} decodeComplete={scan?.decode_complete ?? false}
          durationSeconds={durationSeconds} currentTime={currentTime} processedFrames={processedFrames}
          onSeek={seekUnavailableReason ? undefined : onSeek}
          seekUnavailableReason={seekUnavailableReason}
          onSelectFrame={onSelectFrame} frameSelectionDisabled={frameSelectionDisabled} />
        {loading ? <p className="empty-state" role="status">正在读取已保存的视频进度…</p> : null}
        {error ? <p className="task-error" role="alert">{error}</p> : null}
      </> : null}
      <div className="video-timeline__actions"><button type="button" className="secondary-button" aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}>
        {expanded ? "收起视频候选时间线" : "展开视频候选时间线"}
      </button>{visible ? <button type="button" className="secondary-button" disabled={loading} onClick={() => void load(snapshotRevision)}>刷新时间轴</button> : null}</div>
      {expanded ? (
        <div className="video-timeline__content">
          <h3>视频候选时间线</h3>
          <button type="button" className="secondary-button" disabled={loading} onClick={() => void load(snapshotRevision)}>
            刷新视频证据
          </button>
          <p className="truth-note">以下是自动扫描保存的候选事件，不代表已确认缺陷。</p>
          {loading ? <p className="empty-state">正在加载视频证据…</p> : null}
          {error ? <p className="task-error" role="alert">{error}</p> : null}
          {!loading && currentDetail && !state ? <p className="truth-note">尚无持久化视频扫描结果。</p> : null}
          {selection ? (
            <section aria-label="复检证据选择">
              <h4>复检证据选择</h4>
              <p>待复检候选 {selection.candidate_count} 帧；已选详细复检 {selection.review_frames.length} 帧；未入选 {selection.deferred_count} 帧。</p>
              <p>所需复检未完整入选：{selection.unreviewed_event_count} 个事件、{selection.unreviewed_audit_count} 张周期审计帧。</p>
              <p className="truth-note">场景筛选只调整复检优先级，不确认或排除缺陷；原始候选证据仍保留。入选不代表复检已经完成。</p>
              {selection.scene_errors.length ? (
                <p className="truth-note">场景筛选部分未完成，已按未知场景回退：{selection.scene_errors.join("、")}</p>
              ) : null}
              <details>
                <summary>查看场景建议（{selection.scene_calls} 次筛选调用）</summary>
                {selection.assessments.map((assessment) => (
                  <p key={assessment.frame_index}>
                    帧 {assessment.frame_index} · {SCENE_LABELS[assessment.scene]} · {selection.review_frames.includes(assessment.frame_index) ? "已选详细复检" : "未入选"}
                    <br />{assessment.reason}
                  </p>
                ))}
              </details>
            </section>
          ) : null}
          {storedFrames.length ? (
            <details>
              <summary>选择已存证据帧（{storedFrames.length}）</summary>
              <p className="truth-note">
                只能选择这里已持久化的精确帧；选中后仍需在对话中明确要求一种增强或几何工具，不会自动执行。
              </p>
              <div className="video-frame-picker">
                {storedFrames.map((frame) => (
                  <p key={`${frame.asset.asset_id}-${frame.asset.version}-${frame.frame_index}`}>
                    <a href={assetUrl(frame.asset.asset_id, frame.asset.version)} target="_blank" rel="noreferrer">
                      证据帧 {frame.frame_index} · {frame.timestamp_seconds} 秒
                    </a>
                    {onSelectFrame ? (
                      <button
                        type="button"
                        className="secondary-button"
                        disabled={frameSelectionDisabled}
                        onClick={() => onSelectFrame(frame)}
                      >
                        选择证据帧 {frame.frame_index} 用于工具
                      </button>
                    ) : null}
                  </p>
                ))}
              </div>
            </details>
          ) : null}
          {state ? (
            <>
              <dl className="timeline-summary">
                <div><dt>扫描帧</dt><dd>{state.next_frame}</dd></div>
                <div><dt>质量充分帧</dt><dd>{state.adequate_frames}</dd></div>
                <div><dt>最后扫描时间</dt><dd>{state.last_timestamp_seconds === null ? "未知" : `${state.last_timestamp_seconds} 秒`}</dd></div>
              </dl>
              <p className={!scan || scan.decode_complete ? "muted" : "task-error"}>
                {scan ? scan.decode_complete ? "解码完整" : `解码未完整${scan.error_code ? `：${scan.error_code}` : ""}` : "扫描尚未形成最终记录；当前显示已保存的分块进度。"}
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
              <a href={assetUrl(state.source.asset_id, state.source.version)} target="_blank" rel="noreferrer">打开源视频</a>
            </>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
