"""Deterministic HTML view of stored evidence, with no model or tool invocation."""

from html import escape

from pipe_inspector.contracts.assets import AssetRef
from pipe_inspector.contracts.reports import TaskReport

LABELS = {
    "PL": "破裂",
    "BX": "变形",
    "FS": "腐蚀",
    "BLOCK": "堵塞",
    "AJ": "支管暗接",
    "OTHER": "其他类别",
}
RESULTS = {
    "normal": "正常",
    "defects_found": "发现缺陷",
    "inconclusive": "无法判定",
    "pending": "尚无结论",
}
REVIEWS = {
    "confirmed": "已确认",
    "needs_review": "待人工复核",
    "tentative": "候选待定",
    "rejected": "已驳回",
}


def evidence_link(asset: AssetRef) -> str:
    path = f"/api/assets/{asset.asset_id}/versions/{asset.version}/content"
    return f'<a href="{path}">原始/派生证据 {asset.asset_id} v{asset.version}</a>'


def render_html(report: TaskReport) -> str:
    task, snapshot = report.task, report.task.snapshot
    coverage = snapshot.coverage
    quality = "画质充分" if coverage.sufficient_quality else "画质不足"
    covered = "覆盖充分" if coverage.sufficient_coverage else "覆盖不足"
    audited = "已完成阴性审计" if coverage.negative_audit_complete else "阴性审计未完成"
    lines = [
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        "<title>管网检查报告</title>",
        "<style>body{max-width:900px;margin:2rem auto;padding:0 1rem;font-family:sans-serif;"
        "line-height:1.6}section{border-top:1px solid #ccc;margin-top:1.5rem}"
        "pre{white-space:pre-wrap;overflow-wrap:anywhere}li{margin:.5rem 0}</style></head><body>",
        "<h1>管网检查报告</h1>",
        f"<p>任务：{snapshot.task_id}</p>",
        f"<p>结果：{RESULTS[snapshot.inspection_result]}"
        f"；运行状态：{snapshot.runtime_status.value}</p>",
        f"<p>快照更新时间：{snapshot.updated_at.isoformat()}</p>",
        "<h2>用户要求</h2><pre>" + escape(task.instruction) + "</pre>",
        "<p>" + evidence_link(task.input_asset) + "</p>",
        f"<h2>证据覆盖</h2><p>{covered}；{quality}；{audited}</p>",
    ]
    if snapshot.waiting_reason:
        lines.append("<p>限制/待处理：" + escape(snapshot.waiting_reason) + "</p>")
    lines.append("<h2>检测发现</h2>")
    if not snapshot.findings:
        lines.append("<p>没有已保存的缺陷发现；是否正常以任务结论和覆盖条件为准。</p>")
    for finding in snapshot.findings:
        label = LABELS.get(finding.canonical_label, "待定类别")
        lines.extend(
            [
                "<section>",
                f"<h3>{label} — {REVIEWS[finding.review_status]}</h3>",
                f"<p>发现 ID：{finding.finding_id}；来源：{finding.source.value}</p>",
            ]
        )
        if finding.open_label:
            lines.append("<p>候选名称：" + escape(finding.open_label) + "</p>")
        if finding.score is not None:
            lines.append(f"<p>模型分数：{finding.score:.4f}（非准确率）</p>")
        if finding.confirmed_by:
            lines.append("<p>确认依据：" + escape(finding.confirmed_by) + "</p>")
        for evidence in finding.evidence:
            lines.append("<p>" + evidence_link(evidence.asset) + "</p>")
            if evidence.frame_index is not None:
                lines.append(
                    f"<p>帧 {evidence.frame_index}；原视频时间 {evidence.timestamp_seconds} 秒</p>"
                )
            if evidence.region:
                lines.append("<p>原图像素坐标：" + escape(str(evidence.region.bbox_xyxy)) + "</p>")
        lines.append("</section>")
    lines.append("<h2>工具调用记录</h2><ul>")
    for call in report.tool_calls:
        description = (
            f"{call.tool_name.value} v{call.tool_version}；模型 {call.model_version or '—'}；"
            f"尝试 {call.attempt}；{call.status.value}"
        )
        if call.error:
            description += f"；{call.error.code}：{call.error.message}"
        if call.skip_reason:
            description += f"；跳过原因：{call.skip_reason}"
        lines.append("<li>" + escape(description) + "</li>")
    if not report.tool_calls:
        lines.append("<li>暂无工具调用记录。</li>")
    lines.append("</ul><p>本报告引用已保存的证据与状态，不执行新的检测或自动改写结论。</p>")
    lines.append("</body></html>")
    return "\n".join(lines)
