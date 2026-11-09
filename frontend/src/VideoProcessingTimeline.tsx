import { useEffect, useMemo, useState } from "react";

import { assetUrl } from "./ImageEvidencePanel";
import { LABEL_TEXT } from "./taskPresentation";
import type { VideoAggregationState, VideoEvent, VideoEvidenceFrame } from "./types";
import "./video-processing-timeline.css";

export type VideoProcessingTimelineProps = {
  state: VideoAggregationState | null;
  frames: VideoEvidenceFrame[];
  reviewFrames: number[];
  durationSeconds: number | null;
  currentTime: number;
  processedFrames: number;
  decodeComplete: boolean;
  scanStartSeconds?: number | null;
  seekUnavailableReason?: string;
  onSeek?: (seconds: number) => void;
  onSelectFrame?: (frame: VideoEvidenceFrame) => void;
  frameSelectionDisabled?: boolean;
};

const MAX_FRAME_MARKERS = 80;
const finiteTime = (value: number | null | undefined) => value != null && Number.isFinite(value) && value >= 0 ? value : 0;
const frameKey = (frame: VideoEvidenceFrame) => `${frame.asset.asset_id}:${frame.asset.version}:${frame.frame_index}`;

function clock(seconds: number): string {
  const centiseconds = Math.round(finiteTime(seconds) * 100);
  const minutes = Math.floor(centiseconds / 6000);
  return `${String(minutes).padStart(2, "0")}:${((centiseconds % 6000) / 100).toFixed(2).padStart(5, "0")}`;
}

function eventFrames(event: VideoEvent, frames: VideoEvidenceFrame[]) {
  return frames.filter((frame) => event.evidence.some((evidence) =>
    evidence.frame_index === frame.frame_index && evidence.timestamp_seconds === frame.timestamp_seconds));
}

function packLanes(events: VideoEvent[]) {
  const ends: number[] = [];
  const items = events.map((event) => {
    let lane = ends.findIndex((end) => end < event.start_seconds);
    if (lane === -1) lane = ends.length;
    ends[lane] = event.end_seconds;
    return { event, lane };
  });
  return { items, lanes: ends.length };
}

export function VideoProcessingTimeline({
  state, frames, reviewFrames, durationSeconds, currentTime, processedFrames,
  decodeComplete, scanStartSeconds, seekUnavailableReason, onSeek, onSelectFrame, frameSelectionDisabled = false,
}: VideoProcessingTimelineProps) {
  const [eventId, setEventId] = useState("");
  const [selectedKey, setSelectedKey] = useState("");
  const [imageFailed, setImageFailed] = useState(false);
  const sourceKey = state ? `${state.source.asset_id}:${state.source.version}` : "";
  const events = useMemo(() => state ? [...state.events, ...(state.finalized ? [] : state.active)]
    .sort((left, right) => left.start_seconds - right.start_seconds || left.start_frame - right.start_frame) : [], [state]);
  const orderedFrames = useMemo(() => [...frames].sort((left, right) =>
    left.timestamp_seconds - right.timestamp_seconds || left.frame_index - right.frame_index), [frames]);
  const knownDuration = durationSeconds != null && Number.isFinite(durationSeconds) && durationSeconds > 0 ? durationSeconds : null;
  const lastScanned = state?.last_timestamp_seconds != null && Number.isFinite(state.last_timestamp_seconds) && state.last_timestamp_seconds >= 0 ? state.last_timestamp_seconds : null;
  const firstScanned = lastScanned !== null && scanStartSeconds != null && Number.isFinite(scanStartSeconds) && scanStartSeconds >= 0 && scanStartSeconds <= lastScanned ? scanStartSeconds : null;
  const observedEnd = frames.reduce((latest, frame) => Math.max(latest, finiteTime(frame.timestamp_seconds)),
    events.reduce((latest, event) => Math.max(latest, finiteTime(event.end_seconds)), finiteTime(lastScanned)));
  const end = Math.max(knownDuration ?? 0, observedEnd);
  const canSeek = Boolean(onSeek) && !seekUnavailableReason;
  const percentage = (seconds: number) => end > 0 ? Math.min(100, Math.max(0, finiteTime(seconds) / end * 100)) : 0;
  const playback = Math.min(end, finiteTime(currentTime));
  const selectedEvent = events.find((event) => event.event_id === eventId);
  const selectedFrame = orderedFrames.find((frame) => frameKey(frame) === selectedKey);
  const linkedFrames = selectedEvent ? eventFrames(selectedEvent, orderedFrames) : [];
  const reviewSet = new Set(reviewFrames);
  const groups = [...new Set(events.map((event) => event.label))].map((label) => ({
    label, ...packLanes(events.filter((event) => event.label === label)),
  }));
  const markers = orderedFrames.length <= MAX_FRAME_MARKERS ? orderedFrames :
    Array.from({ length: MAX_FRAME_MARKERS }, (_, index) => orderedFrames[Math.round(index * (orderedFrames.length - 1) / (MAX_FRAME_MARKERS - 1))]);

  useEffect(() => { setEventId(""); setSelectedKey(""); }, [sourceKey]);
  useEffect(() => { setImageFailed(false); }, [selectedKey]);

  function seek(seconds: number) {
    if (canSeek) onSeek?.(finiteTime(seconds));
  }

  function chooseEvent(event: VideoEvent) {
    setEventId(event.event_id);
    const first = eventFrames(event, orderedFrames)[0];
    setSelectedKey(first ? frameKey(first) : "");
    seek(event.start_seconds);
  }

  function chooseFrame(frame: VideoEvidenceFrame, keepEvent = false) {
    if (!keepEvent) setEventId("");
    setSelectedKey(frameKey(frame));
    seek(frame.timestamp_seconds);
  }

  const cursor = canSeek ? <span className="processing-timeline__cursor" style={{ left: `${percentage(playback)}%` }} aria-hidden="true" /> : null;
  return <section className="processing-timeline" aria-label="视频处理时间轴">
    <header className="processing-timeline__heading">
      <h4>视频处理时间轴</h4>
      <span><output aria-label="当前播放时间">{clock(currentTime)}</output> / {knownDuration === null ? "总时长未知" : clock(knownDuration)}</span>
    </header>
    <div className="processing-timeline__summary">
      <span>已处理 {state?.next_frame ?? processedFrames} 帧</span>
      <span>{lastScanned !== null ? `扫描至 ${clock(lastScanned)}` : "时间定位结果尚未保存"}</span>
      {decodeComplete && <span className="processing-timeline__complete">解码完整</span>}
      <span>{events.length} 个候选区间 · {frames.length} 张证据帧</span>
    </div>
    <div className="processing-timeline__axes">
    <div className="processing-timeline__row processing-timeline__scale">
      <span>原视频时间</span>
      <div>
        <input type="range" min={0} max={end || 0} step="0.01" value={playback}
          aria-label="视频播放位置" aria-valuetext={`${clock(playback)}${knownDuration === null ? "，总时长未知" : `，总时长 ${clock(knownDuration)}`}`}
          disabled={!canSeek || end <= 0} onChange={(event) => seek(Number(event.target.value))} />
        <div className="processing-timeline__ticks" aria-hidden="true">
          {[0, .25, .5, .75, 1].map((fraction) => <span key={fraction}>{clock(end * fraction)}</span>)}
        </div>
      </div>
    </div>
    <div className="processing-timeline__row">
      <span>扫描范围</span>
      <div className="processing-timeline__track" role="img" aria-label={lastScanned === null ? "扫描时间范围尚未保存" : firstScanned === null ? `扫描位置已保存至 ${lastScanned} 秒，起点未知` : `已保存扫描范围：${firstScanned} 至 ${lastScanned} 秒`}>
        {firstScanned !== null && lastScanned !== null && <span className="processing-timeline__scanned" style={{ left: `${percentage(firstScanned)}%`, width: `${percentage(lastScanned) - percentage(firstScanned)}%` }} />}
        {firstScanned === null && lastScanned !== null && <span className="processing-timeline__scan-end" style={{ left: `clamp(0px, ${percentage(lastScanned)}%, calc(100% - 2px))` }} />}
        {cursor}
      </div>
    </div>
    {groups.length > 0 ? <div className="processing-timeline__candidates" aria-label="候选时间区间">
      {groups.map(({ label, items, lanes }) => <div className="processing-timeline__row" key={label}>
        <span>{LABEL_TEXT[label]} <small>候选 {items.length}</small></span>
        <div className="processing-timeline__track processing-timeline__event-track" style={{ height: `${lanes * 22 + 4}px` }}>
          {items.map(({ event, lane }) => <button type="button" key={event.event_id}
            className={`processing-timeline__event${event.event_id === eventId ? " is-selected" : ""}`}
            style={{ left: `clamp(0px, ${percentage(event.start_seconds)}%, calc(100% - 8px))`, width: `${Math.max(0, percentage(event.end_seconds) - percentage(event.start_seconds))}%`, top: `${lane * 22 + 3}px` }}
            title={`${LABEL_TEXT[label]}候选 · ${clock(event.start_seconds)}–${clock(event.end_seconds)}`}
            aria-label={`${LABEL_TEXT[label]}候选 ${event.start_seconds} 至 ${event.end_seconds} 秒`}
            aria-pressed={event.event_id === eventId} onClick={() => chooseEvent(event)} />)}
          {cursor}
        </div>
      </div>)}
    </div> : <p className="processing-timeline__empty">{state ? "当前没有已保存的候选区间。" : "扫描时间定位尚未保存，暂不绘制候选区间。"}</p>}
    <div className="processing-timeline__row">
      <span>证据帧 <small>{frames.length}</small></span>
      <div className="processing-timeline__track processing-timeline__frame-track">
        {markers.map((frame) => <button type="button" key={frameKey(frame)}
          className={`processing-timeline__frame${reviewSet.has(frame.frame_index) ? " is-review" : ""}${frameKey(frame) === selectedKey ? " is-selected" : ""}`}
          style={{ left: `clamp(7px, ${percentage(frame.timestamp_seconds)}%, calc(100% - 7px))` }}
          title={`帧 ${frame.frame_index} · ${clock(frame.timestamp_seconds)} · ${reviewSet.has(frame.frame_index) ? "已选复检" : "已保存"}`}
          aria-label={`定位证据帧 ${frame.frame_index}，${frame.timestamp_seconds} 秒${reviewSet.has(frame.frame_index) ? "，已选复检" : ""}`}
          aria-pressed={frameKey(frame) === selectedKey} onClick={() => chooseFrame(frame)} />)}
        {cursor}
      </div>
    </div>
    </div>
    <p className="processing-timeline__legend"><span>■ 已保存扫描范围</span><span>▰ 自动候选</span><span>◆ 已选复检帧</span>候选与复检入选均不代表已确认缺陷。</p>
    {knownDuration === null && <p className="processing-timeline__hint">刻度依据已保存的最晚时间点，不代表完整视频时长。</p>}
    {lastScanned !== null && firstScanned === null && <p className="processing-timeline__hint">扫描起点尚未保存，仅标出已保存的扫描位置。</p>}
    {knownDuration !== null && observedEnd > knownDuration && <p className="processing-timeline__hint">原视频时间戳超出播放时长，轴上保留原始时间，不作换算。</p>}
    {seekUnavailableReason && <p className="processing-timeline__hint">{seekUnavailableReason}</p>}
    {markers.length < frames.length && <p className="processing-timeline__hint">证据点较密，轴上显示 {markers.length} 个定位点；下方可浏览全部 {frames.length} 张证据帧。</p>}
    {(events.length > 0 || frames.length > 0) && <div className="processing-timeline__selectors">
      {events.length > 0 && <label>候选区间<select aria-label="浏览候选区间" value={selectedEvent?.event_id ?? ""}
        onChange={(event) => { const selected = events.find((item) => item.event_id === event.target.value); if (selected) chooseEvent(selected); }}>
        <option value="">选择候选区间</option>
        {events.map((event) => <option key={event.event_id} value={event.event_id}>{LABEL_TEXT[event.label]} · {clock(event.start_seconds)}–{clock(event.end_seconds)}</option>)}
      </select></label>}
      {frames.length > 0 && <label>已存证据帧<select aria-label="浏览已存证据帧" value={selectedFrame ? frameKey(selectedFrame) : ""}
        onChange={(event) => { const selected = orderedFrames.find((frame) => frameKey(frame) === event.target.value); if (selected) chooseFrame(selected); }}>
        <option value="">选择证据帧</option>
        {orderedFrames.map((frame) => <option key={frameKey(frame)} value={frameKey(frame)}>帧 {frame.frame_index} · {clock(frame.timestamp_seconds)}{reviewSet.has(frame.frame_index) ? " · 已选复检" : ""}</option>)}
      </select></label>}
    </div>}
    {(selectedEvent || selectedFrame) && <div className="processing-timeline__selection" aria-label="时间轴选中证据">
      {selectedFrame ? <a className="processing-timeline__preview" href={assetUrl(selectedFrame.asset)} target="_blank" rel="noreferrer">
        {imageFailed ? <span>预览暂不可用，打开原始证据</span> : <img src={assetUrl(selectedFrame.asset)} alt={`证据帧 ${selectedFrame.frame_index}，${selectedFrame.timestamp_seconds} 秒`} onError={() => setImageFailed(true)} />}
      </a> : null}
      <div>
        {selectedEvent && <strong>{LABEL_TEXT[selectedEvent.label]}候选 · {clock(selectedEvent.start_seconds)}–{clock(selectedEvent.end_seconds)}</strong>}
        {selectedFrame ? <>
          <p>证据帧 {selectedFrame.frame_index} · {selectedFrame.timestamp_seconds} 秒 <span>{reviewSet.has(selectedFrame.frame_index) ? "已选复检" : "已保存"}</span></p>
          <div className="processing-timeline__actions"><a href={assetUrl(selectedFrame.asset)} target="_blank" rel="noreferrer">打开原始证据</a>
            {onSelectFrame && <button type="button" disabled={frameSelectionDisabled} onClick={() => onSelectFrame(selectedFrame)}>使用此证据帧</button>}</div>
        </> : <p>该候选尚无已保存的证据帧。</p>}
        {linkedFrames.length > 1 && <div className="processing-timeline__related" aria-label="该候选的已存证据帧">{linkedFrames.map((frame) => <button key={frameKey(frame)} type="button" aria-pressed={selectedKey === frameKey(frame)} onClick={() => chooseFrame(frame, true)}>帧 {frame.frame_index}</button>)}</div>}
      </div>
    </div>}
  </section>;
}
