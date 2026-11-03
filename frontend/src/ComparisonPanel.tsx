import { useEffect, useRef, useState } from "react";

import { ApiError, getComparison } from "./api";
import { LABEL_TEXT, REVIEW_TEXT } from "./taskPresentation";
import type { AssetRef, ComparisonResult, Finding, TaskRecord } from "./types";

type ComparisonPanelProps = {
  sessionId: string;
  requestId: string;
};

function assetUrl(asset: AssetRef): string {
  return `/api/assets/${encodeURIComponent(asset.asset_id)}/versions/${asset.version}/content`;
}

function reportUrl(sessionId: string, requestId: string, format: "json" | "html"): string {
  return `/api/sessions/${encodeURIComponent(sessionId)}/turns/${encodeURIComponent(requestId)}` +
    `/comparison/report?format=${format}`;
}

function evidenceLocator(finding: Finding, index: number): string {
  const evidence = finding.evidence[index];
  if (evidence.frame_index === null) return `证据 ${index + 1}`;
  return `证据 ${index + 1} · 帧 ${evidence.frame_index} · ${evidence.timestamp_seconds} 秒`;
}

function SourceSnapshot({ title, task }: { title: string; task: TaskRecord }) {
  return (
    <section className="comparison-source">
      <h4>{title}</h4>
      <p>
        任务 <code>{task.snapshot.task_id}</code> · {task.snapshot.runtime_status} ·
        {` ${task.snapshot.inspection_result}`}
      </p>
      <p>
        创建 {task.created_at} · 快照 {task.snapshot.updated_at} ·
        {task.snapshot.coverage.sufficient_coverage ? " 覆盖充分" : " 覆盖不足"} ·
        {task.snapshot.coverage.sufficient_quality ? " 画质充分" : " 画质不足"} ·
        {task.snapshot.coverage.negative_audit_complete ? " 阴性审计完成" : " 阴性审计未完成"}
      </p>
      {task.snapshot.waiting_reason ? (
        <p className="truth-note">限制/待处理：{task.snapshot.waiting_reason}</p>
      ) : null}
      <a href={assetUrl(task.input_asset)} target="_blank" rel="noreferrer">打开原始来源</a>
      {task.snapshot.findings.length ? (
        <ul>
          {task.snapshot.findings.map((finding) => (
            <li key={finding.finding_id}>
              <code>{finding.finding_id}</code> · {finding.canonical_label ?? "未分类"}
              {finding.open_label ? ` / ${finding.open_label}` : ""} · {REVIEW_TEXT[finding.review_status]}
              <span className="evidence-links">
                {finding.evidence.map((evidence, index) => (
                  <a
                    key={`${evidence.asset.asset_id}-${evidence.asset.version}-${index}`}
                    href={assetUrl(evidence.asset)}
                    target="_blank"
                    rel="noreferrer"
                  >
                    {evidenceLocator(finding, index)}
                  </a>
                ))}
              </span>
            </li>
          ))}
        </ul>
      ) : <p className="muted">此快照没有已保存发现。</p>}
    </section>
  );
}

export function ComparisonPanel({ sessionId, requestId }: ComparisonPanelProps) {
  const [expanded, setExpanded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<ComparisonResult | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const activeRequest = useRef(0);
  const identity = `${sessionId}:${requestId}`;
  const stateIdentity = useRef(identity);
  const identityChanged = stateIdentity.current !== identity;

  useEffect(() => {
    activeRequest.current += 1;
    stateIdentity.current = identity;
    setExpanded(false);
    setLoading(false);
    setResult(null);
    setLoaded(false);
    setError(null);
    return () => { activeRequest.current += 1; };
  }, [identity]);

  useEffect(() => {
    if (identityChanged || !expanded || loaded) return;
    const request = ++activeRequest.current;
    setLoading(true);
    setError(null);
    void getComparison(sessionId, requestId)
      .then((next) => {
        if (activeRequest.current === request) {
          setResult(next);
          setLoaded(true);
        }
      })
      .catch((nextError: unknown) => {
        if (activeRequest.current === request) {
          setError(nextError instanceof ApiError ? nextError.message : "对比结果加载失败");
        }
      })
      .finally(() => {
        if (activeRequest.current === request) setLoading(false);
      });
  }, [expanded, identityChanged, loaded, requestId, sessionId]);

  const visibleExpanded = expanded && !identityChanged;

  function toggle() {
    if (visibleExpanded) {
      activeRequest.current += 1;
      setExpanded(false);
      setLoading(false);
      setResult(null);
      setLoaded(false);
      setError(null);
    } else {
      setExpanded(true);
    }
  }

  return (
    <section className="comparison-panel">
      <button
        type="button"
        className="secondary-button"
        aria-expanded={visibleExpanded}
        onClick={toggle}
      >
        {visibleExpanded ? "收起保存结果对比" : "展开保存结果对比"}
      </button>
      {visibleExpanded ? (
        <div className="comparison-panel__content">
          <h3>已保存发现对比</h3>
          <p className="truth-note">
            这里只统计两个任务快照中已保存发现。发现匹配未经验证，计数差异不能证明缺陷已修复或出现新缺陷。
          </p>
          {result ? <p>对比快照生成时间：{result.created_at} · 第 {result.attempt} 次尝试</p> : null}
          {loading ? <p className="empty-state">正在读取已保存对比…</p> : null}
          {error ? <p className="task-error" role="alert">{error}</p> : null}
          {!loading && loaded && result === null ? (
            <p className="muted">该对话没有已保存的对比结果。</p>
          ) : null}
          {result ? (
            <>
              <div className="comparison-sources">
                <SourceSnapshot title="参考任务快照" task={result.reference} />
                <SourceSnapshot title="当前任务快照" task={result.current} />
              </div>
              <div className="comparison-table-wrap">
                <table>
                  <caption>七类已保存发现状态计数</caption>
                  <thead>
                    <tr>
                      <th rowSpan={2}>类别</th>
                      <th colSpan={4}>参考任务</th>
                      <th colSpan={4}>当前任务</th>
                      <th rowSpan={2}>已确认差值</th>
                    </tr>
                    <tr>
                      <th>已确认</th><th>暂定</th><th>需复核</th><th>已排除</th>
                      <th>已确认</th><th>暂定</th><th>需复核</th><th>已排除</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.rows.map((row) => (
                      <tr key={row.label}>
                        <th>{row.label} · {LABEL_TEXT[row.label]}</th>
                        <td>{row.reference.confirmed}</td>
                        <td>{row.reference.tentative}</td>
                        <td>{row.reference.needs_review}</td>
                        <td>{row.reference.rejected}</td>
                        <td>{row.current.confirmed}</td>
                        <td>{row.current.tentative}</td>
                        <td>{row.current.needs_review}</td>
                        <td>{row.current.rejected}</td>
                        <td>{row.confirmed_delta > 0 ? "+" : ""}{row.confirmed_delta}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p>匹配状态：未经验证 · 范围：已保存发现</p>
              <div className="report-links" aria-label="保存结果对比报告">
                <a href={reportUrl(sessionId, requestId, "json")} target="_blank" rel="noreferrer">JSON 对比报告</a>
                <a href={reportUrl(sessionId, requestId, "html")} target="_blank" rel="noreferrer">HTML 对比报告</a>
              </div>
            </>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
