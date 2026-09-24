"""Escaped HTML rendering for one read-only conversation snapshot."""

from html import escape

from pipe_inspector.contracts.reports import SessionReport
from pipe_inspector.reports import render_evidence_sections


def _value(value: object) -> str:
    return escape(str(getattr(value, "value", value)))


def render_session_html(report: SessionReport) -> str:
    lines = [
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        "<title>会话统一检查报告</title>",
        "<style>body{max-width:1000px;margin:2rem auto;padding:0 1rem;font-family:sans-serif;"
        "line-height:1.6}section{border-top:1px solid #ccc;margin-top:1.5rem}"
        "article{border-left:3px solid #bbb;padding-left:1rem;margin:1rem 0}"
        "code{overflow-wrap:anywhere}li{margin:.35rem 0}</style></head><body>",
        "<h1>会话统一检查报告</h1>",
        f"<p>会话 ID：<code>{report.session.session_id}</code>；生成时间："
        f"{escape(report.generated_at.isoformat())}</p>",
    ]
    if report.is_final:
        lines.append("<p>当前为最终快照。</p>")
    else:
        lines.append("<p><strong>当前为非最终快照：</strong>运行中的进度和证据可能继续更新。</p>")
    lines.append(
        "<p>各来源保留自己的发现 ID 和状态；辅助任务及重复检查的发现"
        "不跨任务累计为独立缺陷总数。</p>"
    )
    lines.append("<h2>对话轮次</h2><ul>")
    for turn in report.turns:
        linked = f"；关联任务 {turn.linked_task_id}" if turn.linked_task_id else ""
        lines.append(
            f"<li><code>{turn.request_id}</code>；{_value(turn.status)} / {_value(turn.stage)}"
            f"{linked}；{escape(turn.text)}</li>"
        )
        if turn.reply:
            lines.append(f"<li>助手回复：{escape(turn.reply)}</li>")
    if not report.turns:
        lines.append("<li>暂无已保存轮次。</li>")
    lines.append("</ul>")
    for group in report.asset_groups:
        asset = group.input_asset
        path = f"/api/assets/{asset.asset_id}/versions/{asset.version}/content"
        lines.extend(
            [
                "<section>",
                f"<h2>同图来源：<code>{asset.asset_id}</code> v{asset.version}</h2>",
                f'<p><a href="{path}">打开输入资产</a></p>',
            ]
        )
        for source in group.sources:
            task = source.task
            snapshot = task.snapshot
            coverage = snapshot.coverage
            lines.extend(
                [
                    "<article>",
                    f"<h3>来源 <code>{source.task_id}</code></h3>",
                    f"<p>根任务：<code>{source.root_task_id}</code>；角色：{source.role}</p>",
                    f"<p>状态：{_value(snapshot.runtime_status)}；阶段：{_value(snapshot.stage)}；"
                    f"总进度：{snapshot.overall_progress:g}；结果："
                    f"{_value(snapshot.inspection_result)}</p>",
                    f"<p>覆盖：{'充分' if coverage.sufficient_coverage else '不足'}；"
                    f"画质：{'充分' if coverage.sufficient_quality else '不足'}；阴性审计："
                    f"{'完成' if coverage.negative_audit_complete else '未完成'}</p>",
                    f"<p>用户要求：{escape(task.instruction)}</p>",
                    f'<p><a href="/api/tasks/{source.task_id}/report?format=html">'
                    "打开该来源的详细任务报告</a></p>",
                ]
            )
            if snapshot.waiting_reason:
                lines.append(f"<p>限制/待处理：{escape(snapshot.waiting_reason)}</p>")
            lines.extend(render_evidence_sections(source))
            lines.append("<h4>已保存发现</h4><ul>")
            for finding in snapshot.findings:
                lines.append(
                    f"<li><code>{finding.finding_id}</code>；类别 "
                    f"{_value(finding.canonical_label or finding.open_label or '未分类')}；"
                    f"{_value(finding.review_status)}；来源 {_value(finding.source)}</li>"
                )
            if not snapshot.findings:
                lines.append("<li>暂无已保存发现；这不单独证明影像正常。</li>")
            lines.append("</ul><h4>工具调用</h4><ul>")
            for call in source.tool_calls:
                detail = (
                    f"{_value(call.tool_name)} v{escape(call.tool_version)}；"
                    f"尝试 {call.attempt}；{_value(call.status)}"
                )
                if call.error:
                    detail += f"；{escape(call.error.code)}：{escape(call.error.message)}"
                if call.skip_reason:
                    detail += f"；跳过原因：{escape(call.skip_reason)}"
                lines.append(f"<li>{detail}</li>")
            if not source.tool_calls:
                lines.append("<li>暂无工具调用记录。</li>")
            lines.append("</ul></article>")
        lines.append("</section>")
    lines.append("</body></html>")
    return "\n".join(lines)
