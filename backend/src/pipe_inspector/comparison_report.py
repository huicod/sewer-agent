"""Escaped HTML rendering for one immutable saved-findings comparison."""

from html import escape

from pipe_inspector.contracts.assets import AssetRef
from pipe_inspector.contracts.comparison import ComparisonResult
from pipe_inspector.contracts.intake import TaskRecord

LABELS = {
    "AJ": "支管暗接",
    "BX": "变形",
    "FS": "腐蚀",
    "PL": "破裂",
    "BLOCK": "堵塞",
    "OTHER": "其他类别",
    "UNLABELED": "未分类",
}
REVIEWS = {
    "confirmed": "已确认",
    "tentative": "候选待定",
    "needs_review": "待人工复核",
    "rejected": "已驳回",
}
RESULTS = {
    "pending": "尚无结论",
    "normal": "正常",
    "defects_found": "发现缺陷",
    "inconclusive": "无法判定",
}


def _asset_path(asset: AssetRef) -> str:
    return f"/api/assets/{asset.asset_id}/versions/{asset.version}/content"


def _source_section(title: str, task: TaskRecord) -> list[str]:
    snapshot = task.snapshot
    coverage = snapshot.coverage
    lines = [
        '<section class="source">',
        f"<h2>{title}</h2>",
        f"<p>任务 ID：<code>{snapshot.task_id}</code></p>",
        f"<p>运行状态：{escape(snapshot.runtime_status.value)}；"
        f"检查结论：{RESULTS[snapshot.inspection_result.value]}；"
        f"任务创建时间：{escape(task.created_at.isoformat())}；"
        f"快照时间：{escape(snapshot.updated_at.isoformat())}</p>",
        "<p>证据覆盖："
        f"{'充分' if coverage.sufficient_coverage else '不足'}；画质："
        f"{'充分' if coverage.sufficient_quality else '不足'}；阴性审计："
        f"{'已完成' if coverage.negative_audit_complete else '未完成'}</p>",
        f"<p>用户要求：{escape(task.instruction)}</p>",
        f'<p><a href="{_asset_path(task.input_asset)}">打开原始来源</a></p>',
        "<h3>快照中的已保存发现</h3>",
    ]
    if snapshot.waiting_reason:
        lines.append(f"<p>限制/待处理：{escape(snapshot.waiting_reason)}</p>")
    if not snapshot.findings:
        lines.append("<p>没有已保存发现；这本身不证明影像正常。</p>")
    for finding in snapshot.findings:
        label = "UNLABELED" if finding.canonical_label is None else str(finding.canonical_label)
        open_label = (
            f"；候选名称：{escape(finding.open_label)}" if finding.open_label else ""
        )
        lines.extend(
            [
                '<article class="finding">',
                f"<h4>{LABELS[label]} — {REVIEWS[finding.review_status.value]}</h4>",
                f"<p>发现 ID：<code>{finding.finding_id}</code>；"
                f"来源：{escape(finding.source.value)}{open_label}</p>",
            ]
        )
        for evidence in finding.evidence:
            locator = ""
            if evidence.frame_index is not None:
                locator = (
                    f"；帧 {evidence.frame_index}；时间 {evidence.timestamp_seconds:g} 秒"
                )
            region = ""
            if evidence.region is not None:
                box = ", ".join(f"{value:g}" for value in evidence.region.bbox_xyxy)
                region = (
                    f"；区域 ({box}) / {evidence.region.width} × {evidence.region.height} 像素"
                )
            lines.append(
                f'<p><a href="{_asset_path(evidence.asset)}">打开原始/派生证据</a>'
                f"{locator}{region}</p>"
            )
        lines.append("</article>")
    lines.append("</section>")
    return lines


def render_comparison_html(result: ComparisonResult) -> str:
    """Render stored snapshots only; no source reread or finding matching."""
    lines = [
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        "<title>已保存检查结果对比</title>",
        "<style>body{max-width:1100px;margin:2rem auto;padding:0 1rem;font-family:sans-serif;"
        "line-height:1.6}table{width:100%;border-collapse:collapse}th,td{border:1px solid #aaa;"
        "padding:.4rem;text-align:right}th:first-child{text-align:left}.sources{display:grid;"
        "grid-template-columns:repeat(auto-fit,minmax(19rem,1fr));gap:1rem}"
        ".source{border:1px solid #bbb;"
        "padding:1rem}.finding{border-top:1px solid #ddd}code{overflow-wrap:anywhere}</style>",
        "</head><body><h1>已保存检查结果对比</h1>",
        f"<p>请求 ID：<code>{result.request_id}</code>；生成时间："
        f"{escape(result.created_at.isoformat())}；尝试次数：{result.attempt}</p>",
        '<p class="warning"><strong>范围说明：</strong>这里只统计两个任务快照中的已保存发现。'
        "发现匹配未经验证，计数差异不能证明缺陷已修复或出现新缺陷，也不推断维修状态。</p>",
        '<div class="sources">',
        *_source_section("参考任务快照", result.reference),
        *_source_section("当前任务快照", result.current),
        "</div>",
        "<h2>七类状态计数</h2>",
        "<table><thead><tr><th rowspan=\"2\">类别</th><th colspan=\"4\">参考任务</th>"
        "<th colspan=\"4\">当前任务</th><th rowspan=\"2\">已确认差值</th></tr>",
        "<tr><th>已确认</th><th>候选待定</th><th>待人工复核</th><th>已驳回</th>"
        "<th>已确认</th><th>候选待定</th><th>待人工复核</th><th>已驳回</th></tr></thead><tbody>",
    ]
    for row in result.rows:
        lines.append(
            f"<tr><th>{row.label} · {LABELS[row.label]}</th>"
            f"<td>{row.reference.confirmed}</td><td>{row.reference.tentative}</td>"
            f"<td>{row.reference.needs_review}</td><td>{row.reference.rejected}</td>"
            f"<td>{row.current.confirmed}</td><td>{row.current.tentative}</td>"
            f"<td>{row.current.needs_review}</td><td>{row.current.rejected}</td>"
            f"<td>{row.confirmed_delta:+d}</td></tr>"
        )
    lines.extend(
        [
            "</tbody></table>",
            "<p>匹配状态：未经验证（unverified）；统计范围：已保存发现（saved_findings）。</p>",
            "</body></html>",
        ]
    )
    return "".join(lines)
