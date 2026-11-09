import { Tag } from "antd";
import { EvidenceThumbnail } from "./EvidenceThumbnail";
import { assetUrl } from "./ImageEvidencePanel";
import { LABEL_TEXT, REVIEW_TEXT } from "./taskPresentation";
import type { TaskRecord } from "./types";

export function FindingsOverview({ task }: { task: TaskRecord }) {
  const findings = task.snapshot.findings;
  return <section className="findings-overview" aria-label="当前任务发现项">
    <header><h2>发现项与证据</h2><span>{findings.length} 项 · 当前任务</span></header>
    {findings.length ? <div className="findings-overview__scroll"><table>
      <thead><tr><th>类别 / 状态</th><th>位置</th><th>原始证据</th></tr></thead>
      <tbody>{findings.map((finding) => {
        const first = finding.evidence[0];
        const sourceIsVideo = first?.frame_index != null && assetUrl(first.asset) === assetUrl(task.input_asset);
        const label = finding.canonical_label ? LABEL_TEXT[finding.canonical_label] : finding.open_label ?? "未分类";
        return <tr key={finding.finding_id}>
          <td><strong>{label}{finding.canonical_label ? <small> {finding.canonical_label}</small> : null}</strong>
            <Tag>{REVIEW_TEXT[finding.review_status]}</Tag>
            <details><summary>结果依据</summary>
              {finding.score !== null ? <p>原始检测器分数 {finding.score}</p> : null}
              {finding.confirmed_by ? <p>{ { rules: "规则确认", detector_only_rules: "纯 YOLO 检测规则确认", human: "人工确认" }[finding.confirmed_by] }</p> : null}
              {finding.open_label ? <p>{finding.open_label}</p> : null}
              <code>{finding.finding_id}</code>
            </details>
          </td>
          <td>{first?.timestamp_seconds != null ? `${first.timestamp_seconds} 秒` : first?.region ? "图中区域" : "—"}
            {first?.frame_index != null ? <small>帧 {first.frame_index}</small> : null}
          </td>
          <td>{first ? <a href={assetUrl(first.asset)} target="_blank" rel="noreferrer" aria-label={`原始证据 1${first.timestamp_seconds === null ? "" : ` · ${first.timestamp_seconds} 秒`}`}>
            {first.region && !sourceIsVideo ? <EvidenceThumbnail url={assetUrl(first.asset)} region={first.region} /> : null}
            查看证据
          </a> : <span>未附证据</span>}
            {finding.evidence.length > 1 ? <details><summary>更多 {finding.evidence.length - 1} 项</summary>{finding.evidence.slice(1).map((evidence, index) => <a key={index} href={assetUrl(evidence.asset)} target="_blank" rel="noreferrer">原始证据 {index + 2}{evidence.timestamp_seconds != null ? ` · ${evidence.timestamp_seconds} 秒` : ""}</a>)}</details> : null}
          </td>
        </tr>;
      })}</tbody>
    </table></div> : <p>当前任务尚无保存的发现项；检查状态以任务记录为准。</p>}
  </section>;
}
