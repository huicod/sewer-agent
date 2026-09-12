import { useEffect, useRef, useState } from "react";

import { ApiError, getGeometry } from "./api";
import type { AssetRef, GeometryDetail, RuntimeStatus } from "./types";

type GeometryPanelProps = {
  taskId: string;
  snapshotRevision: string;
  runtimeStatus: RuntimeStatus;
  findingIds: string[];
};

function assetUrl(asset: AssetRef): string {
  return `/api/assets/${encodeURIComponent(asset.asset_id)}/versions/${asset.version}/content`;
}

export function GeometryPanel({
  taskId,
  snapshotRevision,
  runtimeStatus,
  findingIds,
}: GeometryPanelProps) {
  const [expanded, setExpanded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [detail, setDetail] = useState<GeometryDetail | null>(null);
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
      const next = await getGeometry(taskId);
      if (activeRequest.current === request) {
        setDetail(next);
        setLoadedRevision(revision);
      }
    } catch (nextError) {
      if (activeRequest.current === request) {
        setError(nextError instanceof ApiError ? nextError.message : "几何证据加载失败");
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
  const segmentation = detail?.segmentation;
  const depth = detail?.depth;
  const measurements = detail?.measurements;

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
    <section className="geometry-panel">
      <button
        type="button"
        className="secondary-button"
        aria-expanded={visibleExpanded}
        onClick={toggle}
      >
        {visibleExpanded ? "收起几何证据" : "展开几何证据"}
      </button>
      {visibleExpanded ? (
        <div className="geometry-panel__content">
          <h3>图像几何证据</h3>
          <p className="truth-note">
            仅显示原图像素统计与单图相对深度；结果需要人工复核，不是实际物理尺寸。
          </p>
          {findingIds.length ? (
            <p className="geometry-finding-ids">
              对话引用发现 ID：{findingIds.map((id) => <code key={id}>{id}</code>)}
            </p>
          ) : null}
          {loading ? <p className="empty-state">正在读取已存几何证据…</p> : null}
          {error ? <p className="task-error" role="alert">{error}</p> : null}
          {!loading && detail && !segmentation && !depth ? (
            <p className="truth-note">
              {runtimeStatus === "running"
                ? "正在处理，暂未生成持久化几何结果。"
                : "该任务没有已存的几何工具结果。"}
            </p>
          ) : null}
          {segmentation ? (
            <section className="geometry-tool" aria-label="提示式掩膜">
              <h4>提示式掩膜</h4>
              <p>提示框采用原图像素坐标，不是掩膜真值。</p>
              <div className="geometry-grid">
                <figure>
                  <svg
                    aria-label="原图与提示框"
                    role="img"
                    viewBox={`0 0 ${segmentation.prompt.width} ${segmentation.prompt.height}`}
                  >
                    <image
                      href={assetUrl(segmentation.input_asset)}
                      width={segmentation.prompt.width}
                      height={segmentation.prompt.height}
                      preserveAspectRatio="xMidYMid meet"
                    />
                    <rect
                      x={segmentation.prompt.bbox_xyxy[0]}
                      y={segmentation.prompt.bbox_xyxy[1]}
                      width={segmentation.prompt.bbox_xyxy[2] - segmentation.prompt.bbox_xyxy[0]}
                      height={segmentation.prompt.bbox_xyxy[3] - segmentation.prompt.bbox_xyxy[1]}
                    />
                  </svg>
                  <figcaption>原图提示框 ({segmentation.prompt.bbox_xyxy.join(", ")})</figcaption>
                </figure>
                <figure>
                  <a href={assetUrl(segmentation.mask_asset.ref)} target="_blank" rel="noreferrer">
                    <img src={assetUrl(segmentation.mask_asset.ref)} alt="二值预测掩膜" loading="lazy" />
                    打开二值掩膜
                  </a>
                </figure>
                <figure>
                  <a href={assetUrl(segmentation.overlay_asset.ref)} target="_blank" rel="noreferrer">
                    <img src={assetUrl(segmentation.overlay_asset.ref)} alt="掩膜叠加图" loading="lazy" />
                    打开叠加图
                  </a>
                </figure>
              </div>
              <p>
                {segmentation.model_id} · {segmentation.model_version} · 掩膜质量估计 {segmentation.model_score}，
                不是缺陷置信度。
              </p>
            </section>
          ) : null}
          {depth ? (
            <section className="geometry-tool" aria-label="单图相对深度">
              <h4>单图相对深度</h4>
              {!segmentation ? (
                <a href={assetUrl(depth.input_asset)} target="_blank" rel="noreferrer">打开原图</a>
              ) : null}
              <div className="geometry-grid">
                <figure>
                  <a href={assetUrl(depth.preview_asset.ref)} target="_blank" rel="noreferrer">
                    <img
                      src={assetUrl(depth.preview_asset.ref)}
                      alt="相对深度预览，白近黑远"
                      loading="lazy"
                    />
                    打开深度预览
                  </a>
                </figure>
              </div>
              <p>图例：白色=近，黑色=远；相对模型单位仅用于本图，不能跨图比较。</p>
              <p>{depth.model_id} · {depth.model_version} · 本图数值范围 {depth.raw_min}–{depth.raw_max}</p>
              <a href={assetUrl(depth.raw_asset.ref)} target="_blank" rel="noreferrer">
                下载 float32 NPY 相对深度数组
              </a>
            </section>
          ) : null}
          {measurements ? (
            <section className="geometry-tool" aria-label="图像平面量化">
              <h4>图像平面量化</h4>
              <p>
                掩膜面积 {measurements.area_pixels} 像素；占全图像素 {measurements.image_plane_fraction * 100}%；
                边界框 {measurements.bbox_width_pixels} × {measurements.bbox_height_pixels} 像素。
              </p>
              {measurements.relative_depth ? (
                <p>
                  掩膜内相对深度：q10 {measurements.relative_depth.q10}；
                  中位数 {measurements.relative_depth.median}；q90 {measurements.relative_depth.q90}；
                  相对模型单位，不能跨图比较。
                </p>
              ) : <p>仅有掩膜像素统计，尚无可组合的相对深度。</p>}
            </section>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
