import { useEffect, useRef, useState } from "react";

import { ApiError, getImageEvidence } from "./api";
import type { AssetRef, EvidenceStrength, Finding, ImageEvidenceDetail } from "./types";

type ImageEvidencePanelProps = {
  taskId: string;
  snapshotRevision: string;
};

const VERDICT_TEXT = {
  accept: "接受原类别",
  revise: "建议改类",
  reject: "建议排除",
  abstain: "无法判断",
  need_more_context: "需要更多上下文",
} as const;

const REVIEW_TEXT = {
  tentative: "暂定",
  confirmed: "已确认",
  rejected: "已排除",
  needs_review: "需复核",
} as const;

function assetUrl(asset: AssetRef): string {
  return `/api/assets/${encodeURIComponent(asset.asset_id)}/versions/${asset.version}/content`;
}

function reportUrl(taskId: string): string {
  return `/api/tasks/${encodeURIComponent(taskId)}/report?format=html`;
}

function strengthText(strength: EvidenceStrength): string {
  if (strength === "clear") return "证据明确";
  if (strength === "uncertain") return "证据不确定";
  return "旧记录未保存证据强度";
}

function sameAsset(left: AssetRef, right: AssetRef): boolean {
  return left.asset_id === right.asset_id && left.version === right.version;
}

type FindingRegion = {
  finding: Finding;
  bbox: [number, number, number, number];
};

function findingRegions(
  detail: ImageEvidenceDetail,
  width: number,
  height: number,
): FindingRegion[] {
  return detail.findings.flatMap((finding) => {
    if (finding.review_status === "rejected") return [];
    const evidence = finding.evidence.find((item) =>
      sameAsset(item.asset, detail.input_asset) && item.region?.width === width && item.region.height === height,
    );
    return evidence?.region ? [{ finding, bbox: evidence.region.bbox_xyxy }] : [];
  });
}

export function ImageEvidencePanel({ taskId, snapshotRevision }: ImageEvidencePanelProps) {
  const [expanded, setExpanded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [detail, setDetail] = useState<ImageEvidenceDetail | null>(null);
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
      const next = await getImageEvidence(taskId);
      if (activeRequest.current === request) {
        setDetail(next);
        setLoadedRevision(revision);
      }
    } catch (nextError) {
      if (activeRequest.current === request) {
        setError(nextError instanceof ApiError ? nextError.message : "检查依据加载失败");
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

  const observation = detail?.observation;
  const dimensions = observation?.detection ?? detail?.findings
    .flatMap((finding) => finding.evidence)
    .find((evidence) => sameAsset(evidence.asset, detail.input_asset) && evidence.region !== null)
    ?.region;
  const effectiveRegions = detail && dimensions
    ? findingRegions(detail, dimensions.width, dimensions.height)
    : [];

  return (
    <section className="image-evidence-panel">
      <button
        type="button"
        className="secondary-button"
        aria-expanded={visibleExpanded}
        onClick={toggle}
      >
        {visibleExpanded ? "收起检查依据" : "展开检查依据"}
      </button>
      {visibleExpanded ? (
        <div className="image-evidence-panel__content">
          <h3>检查依据与结论沿革</h3>
          <p className="truth-note">
            原检测候选、VLM 复核意见、当时保存的融合动作和当前业务发现分开显示；展开不会重新调用模型或改写结论。
          </p>
          {loading ? <p className="empty-state">正在读取已存检查依据…</p> : null}
          {error ? <p className="task-error" role="alert">{error}</p> : null}
          {!loading && detail ? (
            <>
              {detail.parent_task_id ? (
                <p className="truth-note">
                  这是关联任务自己的已存依据；没有独立保存的阶段不会伪装成重新复检。{" "}
                  <a href={reportUrl(detail.parent_task_id)} target="_blank" rel="noreferrer">查看原任务报告</a>
                </p>
              ) : null}

              <section className="provenance-section" aria-label="原始检测结果">
                <h4>1. YOLO / 主检测器原始结果</h4>
                {observation ? (
                  <>
                    <p>
                      {observation.detection.detector_id} · {observation.detection.model_version} ·
                      标签空间 {observation.detection.label_space_version}
                    </p>
                    <svg
                      aria-label="原始检测候选叠加图"
                      role="img"
                      viewBox={`0 0 ${observation.detection.width} ${observation.detection.height}`}
                    >
                      <image
                        href={assetUrl(detail.input_asset)}
                        width={observation.detection.width}
                        height={observation.detection.height}
                        preserveAspectRatio="xMidYMid meet"
                      />
                      {observation.detection.detections.map((detection, index) => (
                        <g key={index}>
                          <rect
                            x={detection.bbox_xyxy[0]}
                            y={detection.bbox_xyxy[1]}
                            width={detection.bbox_xyxy[2] - detection.bbox_xyxy[0]}
                            height={detection.bbox_xyxy[3] - detection.bbox_xyxy[1]}
                          />
                          <text x={detection.bbox_xyxy[0] + 4} y={detection.bbox_xyxy[1] + 16}>
                            #{index} {detection.label}
                          </text>
                        </g>
                      ))}
                    </svg>
                    {observation.detection.detections.length ? (
                      <ul>
                        {observation.detection.detections.map((detection, index) => (
                          <li key={index}>
                            候选 #{index} · {detection.label} · 原始检测器分数 {detection.score}
                            · 原生标签 {detection.native_label} · 原图框 ({detection.bbox_xyxy.join(", ")})
                          </li>
                        ))}
                      </ul>
                    ) : <p className="truth-note">已存检测完成，但原始候选为空。</p>}
                    <p>
                      原图质量：亮度 {observation.quality.mean_luminance} · 对比度 {observation.quality.contrast} ·
                      清晰度 {observation.quality.sharpness} · {observation.quality.adequate ? "质量充分" : "质量不足"}
                    </p>
                  </>
                ) : <p className="truth-note">本任务没有已保存的原始检测观察。</p>}
              </section>

              <section className="provenance-section" aria-label="VLM 复核意见">
                <h4>2. VLM 复核意见</h4>
                {detail.review ? (
                  <>
                    <p>{detail.review.summary}</p>
                    <p>
                      图片{detail.review.image_assessable ? "可判读" : "不可充分判读"}；
                      {detail.review.full_image_audited ? "已保存整图审计" : "未保存完整整图审计"}。
                    </p>
                    {detail.review.candidates.length ? (
                      <ul>
                        {detail.review.candidates.map((candidate) => (
                          <li key={candidate.index}>
                            VLM 候选 #{candidate.index} · {VERDICT_TEXT[candidate.verdict]}
                            {candidate.proposed_label ? ` · 建议 ${candidate.proposed_label}` : ""}
                            {candidate.coexisting_labels?.length
                              ? ` · 共存建议 ${candidate.coexisting_labels.join("、")}`
                              : ""}
                            {candidate.original_presence
                              ? ` · 原类别${candidate.original_presence === "present" ? "存在" : candidate.original_presence === "absent" ? "不存在" : "不确定"}`
                              : ""}
                            · {strengthText(candidate.evidence_strength)}：{candidate.reason}
                          </li>
                        ))}
                      </ul>
                    ) : <p className="truth-note">VLM 没有逐候选意见。</p>}
                    <section aria-label="VLM 新增建议">
                      <h5>候选外新增建议</h5>
                      {detail.review.additional_anomalies.length ? (
                        <ul>
                          {detail.review.additional_anomalies.map((anomaly, index) => (
                            <li key={index}>
                              新增 #{index} · {anomaly.label}{anomaly.name ? ` · ${anomaly.name}` : ""}
                              {anomaly.confirmation_status ? ` · 二次确认${anomaly.confirmation_status === "confirmed" ? "通过" : "不确定"}` : ""}
                              · {strengthText(anomaly.evidence_strength)} ·
                              {anomaly.bbox_xyxy ? ` 原图框 (${anomaly.bbox_xyxy.join(", ")}) ·` : " 无定位框 ·"}
                              {anomaly.reason}
                            </li>
                          ))}
                        </ul>
                      ) : <p className="truth-note">VLM 没有保存候选外新增建议。</p>}
                    </section>
                  </>
                ) : (
                  <p className="truth-note">
                    本任务没有已保存的独立 VLM 复核；可能尚未完成，或历史记录不可用。
                  </p>
                )}
              </section>

              <section className="provenance-section" aria-label="保存的融合动作">
                <h4>3. 当时保存的自动融合动作</h4>
                <p className="truth-note">融合动作是当时的自动处理记录，不等同于当前人工审核后的发现。</p>
                {detail.fusion_changes.length ? (
                  <ul>
                    {detail.fusion_changes.map((change, position) => (
                      <li key={position}>
                        {change.index !== null ? `候选 #${change.index}` : `新增 #${change.additional_index}`}
                        {` · ${change.action} · 输出标签 ${change.label} · ${change.pending ? "待人工处理" : "已自动处理"}`}
                        {change.coexisting_labels?.length
                          ? ` · 共存建议 ${change.coexisting_labels.join("、")}`
                          : ""}
                        {change.original_presence ? ` · 原类别${change.original_presence}` : ""}
                        {change.confirmation_status ? ` · 二次确认 ${change.confirmation_status}` : ""}
                        {`：${change.reason}`}{change.gate ? `；门控：${change.gate}` : ""}
                      </li>
                    ))}
                  </ul>
                ) : <p className="truth-note">历史融合动作未保存；此处不会重新计算。</p>}
              </section>

              <section className="provenance-section" aria-label="当前业务发现">
                <h4>4. 当前业务发现</h4>
                <p className="truth-note">这是当前任务快照，包含后续人工审核状态；已排除项不绘制为当前有效缺陷。</p>
                {dimensions && effectiveRegions.length ? (
                  <svg
                    className="current-findings-overlay"
                    aria-label="当前有效发现叠加图"
                    role="img"
                    viewBox={`0 0 ${dimensions.width} ${dimensions.height}`}
                  >
                    <image
                      href={assetUrl(detail.input_asset)}
                      width={dimensions.width}
                      height={dimensions.height}
                      preserveAspectRatio="xMidYMid meet"
                    />
                    {effectiveRegions.map(({ finding, bbox }) => (
                      <g key={finding.finding_id}>
                        <rect
                          data-review-status={finding.review_status}
                          x={bbox[0]}
                          y={bbox[1]}
                          width={bbox[2] - bbox[0]}
                          height={bbox[3] - bbox[1]}
                        />
                        <text x={bbox[0] + 4} y={bbox[1] + 16}>
                          {finding.canonical_label ?? "未分类"} {REVIEW_TEXT[finding.review_status]} {finding.finding_id.slice(0, 8)}
                        </text>
                      </g>
                    ))}
                  </svg>
                ) : <p className="truth-note">当前没有可在原图上绘制的非排除发现。</p>}
                {detail.findings.length ? (
                  <ul>
                    {detail.findings.map((finding) => (
                      <li key={finding.finding_id}>
                        {finding.finding_id} · {finding.canonical_label ?? "未分类"}
                        {finding.open_label ? ` · ${finding.open_label}` : ""} · {REVIEW_TEXT[finding.review_status]}
                      </li>
                    ))}
                  </ul>
                ) : <p className="truth-note">当前任务快照没有发现项。</p>}
              </section>
            </>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
