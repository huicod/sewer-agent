import { useEffect, useRef, useState } from "react";

import { ApiError, getEnhancement } from "./api";
import type { EnhancementDetail, QualitySummary, RuntimeStatus } from "./types";

type EnhancementPanelProps = {
  taskId: string;
  snapshotRevision: string;
  runtimeStatus: RuntimeStatus;
};

const KIND_TEXT = {
  dehaze: "去雾",
  deblur: "去模糊",
  super_resolution: "超分辨率",
} as const;

const EFFECT_TEXT = {
  improved: "改善",
  unchanged: "无明显变化",
  degraded: "下降",
  uncertain: "不确定",
} as const;

function assetUrl(assetId: string, version: number): string {
  return `/api/assets/${encodeURIComponent(assetId)}/versions/${version}/content`;
}

function Quality({ label, value }: { label: string; value: QualitySummary }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>
        亮度 {value.mean_luminance} · 对比度 {value.contrast} · 清晰度 {value.sharpness}
        · {value.adequate ? "质量充分" : "质量不足"}
      </dd>
    </div>
  );
}

export function EnhancementPanel({ taskId, snapshotRevision, runtimeStatus }: EnhancementPanelProps) {
  const [expanded, setExpanded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [detail, setDetail] = useState<EnhancementDetail | null>(null);
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
    setDetail(null);
    try {
      const next = await getEnhancement(taskId);
      if (activeRequest.current === request) {
        setDetail(next);
        setLoadedRevision(revision);
      }
    } catch (nextError) {
      if (activeRequest.current === request) {
        setError(nextError instanceof ApiError ? nextError.message : "图片增强详情加载失败");
      }
    } finally {
      if (activeRequest.current === request) setLoading(false);
    }
  }

  useEffect(() => {
    if (taskChanged || !expanded || loadedRevision === snapshotRevision) return;
    void load(snapshotRevision);
  }, [taskId, snapshotRevision, expanded, loadedRevision]);

  const visibleExpanded = expanded && !taskChanged;
  const result = detail?.result;
  const review = detail?.review;

  function toggle() {
    if (visibleExpanded) {
      activeRequest.current += 1;
      setExpanded(false);
      setLoading(false);
      setDetail(null);
      setError(null);
      setLoadedRevision(null);
    } else {
      setExpanded(true);
    }
  }

  return (
    <section className="enhancement-panel">
      <button
        type="button"
        className="secondary-button"
        aria-expanded={visibleExpanded}
        onClick={toggle}
      >
        {visibleExpanded ? "收起图片增强对照" : "展开图片增强对照"}
      </button>
      {visibleExpanded ? (
        <div className="enhancement-panel__content">
          <h3>图片增强对照</h3>
          <p className="truth-note">增强图是派生证据，仅供辅助复核，不改写原检查结论。</p>
          {loading ? <p className="empty-state">正在读取已存增强证据…</p> : null}
          {error ? <p className="task-error" role="alert">{error}</p> : null}
          {!loading && detail && !result ? (
            <p className="truth-note">
              {runtimeStatus === "running"
                ? "正在处理，暂未生成持久化增强结果。"
                : "该任务没有已存的图片增强结果。"}
            </p>
          ) : null}
          {result ? (
            <>
              <div className="enhancement-comparison">
                <figure>
                  <svg
                    aria-label="原图与处理范围"
                    role="img"
                    viewBox={`0 0 ${result.source_region.width} ${result.source_region.height}`}
                  >
                    <image
                      href={assetUrl(result.input_asset.asset_id, result.input_asset.version)}
                      width={result.source_region.width}
                      height={result.source_region.height}
                      preserveAspectRatio="xMidYMid meet"
                    />
                    <rect
                      x={result.source_region.bbox_xyxy[0]}
                      y={result.source_region.bbox_xyxy[1]}
                      width={result.source_region.bbox_xyxy[2] - result.source_region.bbox_xyxy[0]}
                      height={result.source_region.bbox_xyxy[3] - result.source_region.bbox_xyxy[1]}
                    />
                  </svg>
                  <figcaption>
                    <a href={assetUrl(result.input_asset.asset_id, result.input_asset.version)} target="_blank" rel="noreferrer">
                      打开原图
                    </a>
                  </figcaption>
                </figure>
                <figure>
                  <a href={assetUrl(result.output_asset.ref.asset_id, result.output_asset.ref.version)} target="_blank" rel="noreferrer">
                    <img
                      src={assetUrl(result.output_asset.ref.asset_id, result.output_asset.ref.version)}
                      alt="增强派生图"
                      loading="lazy"
                    />
                    打开增强图
                  </a>
                </figure>
              </div>
              <p>
                原图 ROI（原始像素坐标）：({result.source_region.bbox_xyxy.join(", ")})；
                原图 ROI 是实际处理范围，增强图尺寸可与原图不同。
              </p>
              <p>{KIND_TEXT[result.kind]} · {result.model_id} · {result.model_version}</p>
              <dl className="enhancement-quality">
                <Quality label="增强前" value={result.quality_before} />
                <Quality label="增强后" value={result.quality_after} />
              </dl>
              {review ? (
                <section className="enhancement-review" aria-label="增强辅助复核">
                  <h4>辅助复核</h4>
                  <p>质量变化：{EFFECT_TEXT[review.quality_effect]}</p>
                  <p>{review.summary}</p>
                  <p>冲突发现 {review.conflicting_finding_ids.length} 项；新增疑似异常 {review.additional_anomalies.length} 项，均需按原图坐标人工确认。</p>
                </section>
              ) : <p className="truth-note">增强结果已保存，辅助复核尚未生成。</p>}
            </>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
