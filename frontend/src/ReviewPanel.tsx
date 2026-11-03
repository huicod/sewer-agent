import { useEffect, useMemo, useRef, useState } from "react";

import { ApiError, getReview, submitReview } from "./api";
import { EvidenceThumbnail } from "./EvidenceThumbnail";
import type {
  AssetRef,
  DetectionLabel,
  Finding,
  FindingReview,
  FindingReviewAction,
  HumanReviewDetail,
  HumanReviewSubmission,
  RuntimeStatus,
  TaskSnapshot,
} from "./types";

const LABELS: DetectionLabel[] = ["AJ", "BX", "FS", "PL", "BLOCK", "OTHER"];
const ACTION_TEXT: Record<FindingReviewAction, string> = {
  confirm: "确认",
  reject: "拒绝",
  relabel: "改标",
};

type DraftDecision = {
  action: FindingReviewAction | "";
  label: DetectionLabel | "";
  openLabel: string;
  reason: string;
};

type ReviewPanelProps = {
  taskId: string;
  snapshotRevision: string;
  runtimeStatus: RuntimeStatus;
  sourceAsset: AssetRef;
  onAccepted: (snapshot: TaskSnapshot) => void;
};

function projectionKey(taskId: string, snapshotRevision: string, runtimeStatus: RuntimeStatus): string {
  return `${taskId}:${snapshotRevision}:${runtimeStatus}`;
}

function assetUrl(assetId: string, version: number): string {
  return `/api/assets/${encodeURIComponent(assetId)}/versions/${version}/content`;
}

function errorText(error: unknown): string {
  return error instanceof ApiError ? error.message : "发生未知错误，请稍后重试";
}

function emptyDraft(findings: Finding[]): Record<string, DraftDecision> {
  return Object.fromEntries(findings.map((finding) => [finding.finding_id, {
    action: "",
    label: "",
    openLabel: finding.canonical_label === "OTHER" ? finding.open_label ?? "" : "",
    reason: "",
  }]));
}

function decisionFromDraft(finding: Finding, draft: DraftDecision): FindingReview {
  const relabel = draft.action === "relabel";
  const currentOther = draft.action === "confirm" && finding.canonical_label === "OTHER";
  return {
    finding_id: finding.finding_id,
    action: draft.action as FindingReviewAction,
    label: relabel ? draft.label as DetectionLabel : null,
    open_label: relabel && draft.label === "OTHER"
      ? draft.openLabel.trim()
      : currentOther
        ? draft.openLabel.trim()
        : null,
    reason: draft.reason.trim(),
  };
}

function isComplete(finding: Finding, draft: DraftDecision | undefined): boolean {
  if (!draft?.action || !draft.reason.trim()) return false;
  if (draft.action === "relabel" && !draft.label) return false;
  if (
    (draft.action === "relabel" && draft.label === "OTHER") ||
    (draft.action === "confirm" && finding.canonical_label === "OTHER")
  ) return Boolean(draft.openLabel.trim());
  return true;
}

function EvidenceView({ finding }: { finding: Finding }) {
  return (
    <div className="review-evidence">
      {finding.evidence.map((evidence, index) => {
        const label = `${finding.canonical_label ?? "未分类"} 原始证据 ${index + 1}`;
        return (
          <figure key={`${evidence.asset.asset_id}-${evidence.asset.version}-${index}`}>
            {evidence.region ? (
              <EvidenceThumbnail url={assetUrl(evidence.asset.asset_id, evidence.asset.version)}
                region={evidence.region} label={label} />
            ) : (
              <a
                aria-label={label}
                href={assetUrl(evidence.asset.asset_id, evidence.asset.version)}
                target="_blank"
                rel="noreferrer"
              >
                打开原始证据 {index + 1}
              </a>
            )}
            <figcaption>
              {evidence.timestamp_seconds === null ? "原始图片" : `${evidence.timestamp_seconds} 秒`}
              {evidence.frame_index === null ? "" : ` · 帧 ${evidence.frame_index}`}
            </figcaption>
          </figure>
        );
      })}
    </div>
  );
}

function AuditRecord({ detail }: { detail: HumanReviewDetail }) {
  if (!detail.record) {
    return <p className="truth-note">该历史任务没有可用的结构化人工审核记录。</p>;
  }
  const { submission, reviewed_at: reviewedAt } = detail.record;
  return (
    <section className="review-audit" aria-label="审核记录">
      <h4>审核记录</h4>
      <p>审核人：{submission.reviewer} · 审核时间：{reviewedAt}</p>
      <ul>
        {submission.decisions.map((decision) => (
          <li key={decision.finding_id}>
            <strong>{ACTION_TEXT[decision.action]}</strong>
            {decision.label ? ` · ${decision.label}${decision.open_label ? `（${decision.open_label}）` : ""}` : ""}
            <span>{decision.reason}</span>
          </li>
        ))}
      </ul>
      <p>阴性审计：{submission.negative_audit_complete ? "已声明完成" : "未声明完成"}</p>
      {submission.note ? <p>{submission.note}</p> : null}
    </section>
  );
}

export function ReviewPanel({ taskId, snapshotRevision, runtimeStatus, sourceAsset, onAccepted }: ReviewPanelProps) {
  const [detail, setDetail] = useState<HumanReviewDetail | null>(null);
  const [drafts, setDrafts] = useState<Record<string, DraftDecision>>({});
  const [reviewer, setReviewer] = useState("huicod");
  const [note, setNote] = useState("");
  const [negativeAudit, setNegativeAudit] = useState(false);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const activeProjection = useRef(projectionKey(taskId, snapshotRevision, runtimeStatus));
  const loadedProjection = useRef<string | null>(null);
  const mounted = useRef(true);
  const pending = useRef<{ taskId: string; signature: string; requestId: string } | null>(null);
  activeProjection.current = projectionKey(taskId, snapshotRevision, runtimeStatus);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => {
    const requestedProjection = projectionKey(taskId, snapshotRevision, runtimeStatus);
    if (loadedProjection.current === requestedProjection) return;
    let stale = false;
    setLoading(true);
    setSubmitting(false);
    setError(null);
    setDetail(null);
    setDrafts({});
    setReviewer("huicod");
    setNote("");
    setNegativeAudit(false);
    pending.current = null;
    void getReview(taskId)
      .then((next) => {
        if (stale || !mounted.current || activeProjection.current !== requestedProjection) return;
        loadedProjection.current = projectionKey(
          next.snapshot.task_id,
          next.snapshot.updated_at,
          next.snapshot.runtime_status,
        );
        setDetail(next);
        setDrafts(emptyDraft(next.snapshot.findings));
      })
      .catch((nextError: unknown) => {
        if (!stale && mounted.current && activeProjection.current === requestedProjection) {
          setError(errorText(nextError));
        }
      })
      .finally(() => {
        if (!stale && mounted.current && activeProjection.current === requestedProjection) setLoading(false);
      });
    return () => { stale = true; };
  }, [taskId, snapshotRevision, runtimeStatus]);

  const decisions = useMemo(() => detail?.snapshot.findings.map((finding) =>
    decisionFromDraft(finding, drafts[finding.finding_id] ?? emptyDraft([finding])[finding.finding_id]),
  ) ?? [], [detail, drafts]);
  const complete = Boolean(
    detail?.can_review && reviewer.trim() &&
    detail.snapshot.findings.every((finding) => isComplete(finding, drafts[finding.finding_id])),
  );
  const signature = complete && detail ? JSON.stringify({
    reviewer: reviewer.trim(),
    snapshot_updated_at: detail.snapshot.updated_at,
    decisions,
    negative_audit_complete: negativeAudit,
    note: note.trim(),
  }) : "";
  const exactRetry = Boolean(error && pending.current?.taskId === taskId && pending.current.signature === signature);

  function updateDraft(findingId: string, patch: Partial<DraftDecision>) {
    setDrafts((current) => ({
      ...current,
      [findingId]: { ...current[findingId], ...patch },
    }));
    setError(null);
  }

  async function submit() {
    if (!detail || !complete || submitting) return;
    const submittedProjection = activeProjection.current;
    const requestId = pending.current?.taskId === taskId && pending.current.signature === signature
      ? pending.current.requestId
      : crypto.randomUUID();
    pending.current = { taskId, signature, requestId };
    const submission: HumanReviewSubmission = {
      request_id: requestId,
      reviewer: reviewer.trim(),
      snapshot_updated_at: detail.snapshot.updated_at,
      decisions,
      negative_audit_complete: negativeAudit,
      note: note.trim(),
    };
    setSubmitting(true);
    setError(null);
    try {
      const record = await submitReview(taskId, submission);
      if (!mounted.current || activeProjection.current !== submittedProjection) return;
      pending.current = null;
      loadedProjection.current = projectionKey(
        record.after.task_id,
        record.after.updated_at,
        record.after.runtime_status,
      );
      setDetail({ task_id: taskId, can_review: false, snapshot: record.after, record });
      onAccepted(record.after);
    } catch (nextError) {
      if (mounted.current && activeProjection.current === submittedProjection) setError(errorText(nextError));
    } finally {
      if (mounted.current && activeProjection.current === submittedProjection) setSubmitting(false);
    }
  }

  if (loading) return <p className="empty-state">正在加载人工复核…</p>;
  if (!detail) return <p className="task-error" role="alert">{error ?? "人工复核详情不可用"}</p>;

  return (
    <section className="review-panel">
      <h3>人工复核</h3>
      <p className="truth-note">结论由服务器根据完整审核集生成；此处不会在浏览器中推导最终结论。</p>
      <a
        className="source-asset-link"
        href={assetUrl(sourceAsset.asset_id, sourceAsset.version)}
        target="_blank"
        rel="noreferrer"
      >
        打开原始输入
      </a>
      {detail.snapshot.findings.map((finding) => {
        const draft = drafts[finding.finding_id];
        return (
          <article className="review-finding" data-testid={`review-finding-${finding.finding_id}`} key={finding.finding_id}>
            <div className="review-finding__heading">
              <strong>{finding.canonical_label ?? "未分类"}{finding.open_label ? ` · ${finding.open_label}` : ""}</strong>
              <span>{finding.review_status} · 来源 {finding.source}</span>
            </div>
            <EvidenceView finding={finding} />
            {detail.can_review ? (
              <fieldset disabled={submitting}>
                <legend>处理动作</legend>
                {(["confirm", "reject", "relabel"] as FindingReviewAction[]).map((action) => (
                  <label key={action}>
                    <input
                      type="radio"
                      name={`action-${finding.finding_id}`}
                      disabled={action === "confirm" && finding.canonical_label === null}
                      checked={draft?.action === action}
                      onChange={() => updateDraft(finding.finding_id, { action })}
                    />
                    {ACTION_TEXT[action]}
                  </label>
                ))}
                {draft?.action === "relabel" ? (
                  <label className="field">
                    <span>新标签</span>
                    <select value={draft.label} onChange={(event) => updateDraft(finding.finding_id, { label: event.target.value as DetectionLabel | "" })}>
                      <option value="">请选择</option>
                      {LABELS.map((label) => <option value={label} key={label}>{label}</option>)}
                    </select>
                  </label>
                ) : null}
                {(draft?.action === "relabel" && draft.label === "OTHER") ||
                (draft?.action === "confirm" && finding.canonical_label === "OTHER") ? (
                  <label className="field">
                    <span>OTHER 名称</span>
                    <input value={draft.openLabel} onChange={(event) => updateDraft(finding.finding_id, { openLabel: event.target.value })} />
                  </label>
                ) : null}
                <label className="field">
                  <span>处理理由</span>
                  <textarea value={draft?.reason ?? ""} maxLength={1000} rows={2} onChange={(event) => updateDraft(finding.finding_id, { reason: event.target.value })} />
                </label>
              </fieldset>
            ) : null}
          </article>
        );
      })}
      {detail.can_review ? (
        <div className="review-form">
          <label className="field">
            <span>审核人（自报身份）</span>
            <input value={reviewer} maxLength={100} disabled={submitting} onChange={(event) => { setReviewer(event.target.value); setError(null); }} />
          </label>
          <label className="field">
            <span>审核备注（可选）</span>
            <textarea value={note} maxLength={2000} rows={2} disabled={submitting} onChange={(event) => { setNote(event.target.value); setError(null); }} />
          </label>
          <label className="check-field">
            <input type="checkbox" checked={negativeAudit} disabled={submitting} onChange={(event) => { setNegativeAudit(event.target.checked); setError(null); }} />
            已完成人工阴性审计
          </label>
          {error ? <p className="task-error" role="alert">{error}</p> : null}
          <button type="button" disabled={!complete || submitting} onClick={() => void submit()}>
            {submitting ? "正在提交…" : exactRetry ? "重新提交完整复核" : "提交完整复核"}
          </button>
        </div>
      ) : <AuditRecord detail={detail} />}
    </section>
  );
}
