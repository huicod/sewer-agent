import { useEffect, useRef, useState } from "react";

import { getAsset } from "./api";
import { assetUrl, findingRegions } from "./ImageEvidencePanel";
import { LABEL_TEXT, REVIEW_TEXT } from "./taskPresentation";
import type { AssetRecord, AssetRef, EvidenceRef, Finding, TaskRecord } from "./types";
import "./media-preview.css";

const pending = new Map<string, Promise<AssetRecord>>();

function loadAsset(ref: AssetRef) {
  const url = assetUrl(ref);
  const previous = pending.get(url);
  if (previous) return previous;
  const request = getAsset(ref).finally(() => pending.delete(url));
  pending.set(url, request);
  return request;
}

function regionSummary(regions: ReturnType<typeof findingRegions>) {
  const groups = new Map<string, { count: number; statuses: Map<string, number> }>();
  for (const { finding } of regions) {
    const { canonical_label, open_label } = finding;
    const label = canonical_label && !(canonical_label === "OTHER" && open_label)
      ? `${canonical_label} · ${LABEL_TEXT[canonical_label]}` : open_label ?? LABEL_TEXT.UNLABELED;
    const group = groups.get(label) ?? { count: 0, statuses: new Map<string, number>() };
    const status = reviewLabel(finding);
    group.count++;
    group.statuses.set(status, (group.statuses.get(status) ?? 0) + 1);
    groups.set(label, group);
  }
  return groups;
}

function reviewLabel(finding: Finding) {
  if (finding.review_status === "confirmed" && finding.confirmed_by) {
    return { detector_only_rules: "纯 YOLO 规则确认", rules: "规则确认", human: "人工确认" }[finding.confirmed_by];
  }
  return REVIEW_TEXT[finding.review_status];
}

function firstEvidenceFrames(task: TaskRecord) {
  const unique = new Map<string, EvidenceRef>();
  const original = assetUrl(task.input_asset);
  for (const finding of task.snapshot.findings) {
    for (const ref of finding.evidence) {
      const url = assetUrl(ref.asset);
      if (ref.frame_index !== null && ref.timestamp_seconds !== null && url !== original && !unique.has(url)) {
        unique.set(url, ref);
      }
    }
  }
  return [...unique.values()].sort((a, b) => a.frame_index! - b.frame_index!).slice(0, 3);
}

function FrameLink({ frame }: { frame: EvidenceRef }) {
  const [broken, setBroken] = useState(false);
  const url = assetUrl(frame.asset);
  return (
    <a className="task-input-preview__frame" href={url} target="_blank" rel="noreferrer">
      {broken ? <small>预览不可用</small> :
        <img src={url} alt="" loading="lazy" onError={() => setBroken(true)} />}
      <span>帧 {frame.frame_index} · {frame.timestamp_seconds} 秒</span>
    </a>
  );
}

export function TaskInputPreview({ task }: { task: TaskRecord }) {
  return <InputPreview key={assetUrl(task.input_asset)} task={task} />;
}

function InputPreview({ task }: { task: TaskRecord }) {
  const container = useRef<HTMLElement>(null);
  const [asset, setAsset] = useState<AssetRecord | null>();
  const [unavailable, setUnavailable] = useState(false);
  const { asset_id, version } = task.input_asset;
  const url = assetUrl(task.input_asset);

  useEffect(() => {
    let active = true;
    function load() {
      loadAsset({ asset_id, version }).then(
        (value) => { if (active) setAsset(value); },
        () => { if (active) setAsset(null); },
      );
    }
    let observer: IntersectionObserver | undefined;
    if (container.current && typeof IntersectionObserver !== "undefined") {
      observer = new IntersectionObserver((entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          observer?.disconnect();
          load();
        }
      }, { rootMargin: "240px" });
      observer.observe(container.current);
    } else {
      load();
    }
    return () => { active = false; observer?.disconnect(); };
  }, [asset_id, version]);

  const video = asset?.kind === "video";
  const image = asset?.media_type.startsWith("image/");
  const label = video ? "输入视频" : image ? "输入图片" : "输入文件";
  const width = asset?.width;
  const height = asset?.height;
  const regions = image && width && height
    ? findingRegions({ findings: task.snapshot.findings, input_asset: task.input_asset }, width, height)
    : [];
  const groups = regionSummary(regions);
  const frames = video ? firstEvidenceFrames(task) : [];

  return (
    <figure className="task-input-preview" ref={container} aria-label={label}>
      <figcaption>
        <span><strong>{label}</strong>
          {width && height ? <small>{width} × {height}</small> : null}
          {video && asset.duration_seconds != null ? <small>{Number(asset.duration_seconds.toFixed(1))} 秒</small> : null}
        </span>
        <a href={url} target="_blank" rel="noreferrer">
          {video ? "打开原视频" : image ? "打开原图" : "打开输入文件"}
        </a>
      </figcaption>
      <div className={`task-input-preview__body${groups.size || frames.length ? " has-evidence" : ""}`}>
      {asset === undefined ? <p>正在准备预览…</p> :
        unavailable || (!video && !image) ? <p>预览暂不可用，可打开输入文件查看。</p> :
        video ? (
          <div className="task-input-preview__stage">
            <video aria-label="输入视频预览" src={url} controls playsInline preload="metadata"
              width={width ?? undefined} height={height ?? undefined} onError={() => setUnavailable(true)} />
          </div>
        ) : (
          <div className="task-input-preview__stage">
            <a className="task-input-preview__image" aria-label="查看输入图片原图" href={url} target="_blank" rel="noreferrer">
              <img src={url} alt="检查任务的输入图片" loading="lazy" onError={() => setUnavailable(true)} />
              {regions.length > 0 && width && height ? (
                <svg viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
                  {regions.map(({ finding, bbox: [left, top, right, bottom] }) => (
                    <g key={finding.finding_id} data-status={finding.review_status}>
                      <rect x={left} y={top} width={right - left} height={bottom - top} />
                      <text x={left} y={top < height * 0.06 ? bottom : top}
                        fontSize={width * 0.034}>
                        {finding.canonical_label ?? finding.open_label ?? "未分类"}
                      </text>
                    </g>
                  ))}
                </svg>
              ) : null}
            </a>
          </div>
        )}
      {groups.size > 0 && (
        <div className="task-input-preview__side" aria-label="图中标注">
          <strong>图中标注 · {regions.length} 项</strong>
          <ul>{[...groups].map(([label, group]) => (
            <li key={label}>
              <span>{label}</span><b>× {group.count}</b>
              <small>{[...group.statuses].map(([status, n]) => group.statuses.size > 1 ? `${status} ${n} 项` : status).join("、")}</small>
            </li>
          ))}</ul>
        </div>
      )}
      {frames.length > 0 && (
        <div className="task-input-preview__side" aria-label="视频证据帧">
          <strong>证据帧预览</strong>
          {frames.map((frame) => <FrameLink key={assetUrl(frame.asset)} frame={frame} />)}
        </div>
      )}
      </div>
    </figure>
  );
}
