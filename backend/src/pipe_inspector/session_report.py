"""Escaped HTML rendering for one read-only conversation snapshot."""

from html import escape

from pipe_inspector.contracts.reports import SessionReport
from pipe_inspector.report_document import document_end, document_start, metadata_table
from pipe_inspector.reports import (
    RESULTS,
    STATUSES,
    evidence_link,
    render_evidence_sections,
    render_findings,
    render_source_scope,
    render_task_summary,
    render_tool_calls,
)

ROLES = {"primary": "原始检查", "supplement": "关联追加复检", "video_frame_tool": "视频证据帧工具"}


def _value(value: object) -> str:
    return escape(str(getattr(value, "value", value)))


def render_session_html(report: SessionReport) -> str:
    lines = document_start("管道影像检查报告", "会话统一检查报告 · 按原始影像与来源任务编排")
    source_count = sum(len(group.sources) for group in report.asset_groups)
    lines.extend([
        '<nav class="report-outline" aria-label="报告目录">'
        '<a href="#basic">基本信息</a><a href="#sources">检查来源</a>'
        '<a href="#evidence">结果与证据</a><a href="#turns">对话记录</a></nav>',
        '<h2 id="basic">1. 基本信息</h2>',
        metadata_table([
            ("会话编号", str(report.session.session_id),
             "报告生成时间", report.generated_at.isoformat()),
            ("会话创建时间", report.session.created_at.isoformat(), "快照状态",
             "当前为最终快照" if report.is_final else "当前为非最终快照"),
            ("输入影像版本组", str(len(report.asset_groups)), "来源任务", str(source_count)),
            ("已保存对话轮次", str(len(report.turns)), "数据版本", report.schema_version),
        ]),
    ])
    if report.is_final:
        lines.append('<p class="report-note">当前为最终快照。任务结论、待定事项及审核依据'
                     "以各来源记录为准。</p>")
    else:
        lines.append('<p class="report-note report-note--pending"><strong>当前为非最终快照：'
                     "</strong>运行中的进度和证据可能继续更新。</p>")
    lines.append(
        '<p class="report-note">各来源保留自己的发现 ID 和状态；辅助任务及重复检查的发现'
        "不跨任务累计为独立缺陷总数。</p>"
    )
    lines.append('<h2 id="sources">2. 检查来源</h2>')
    if report.asset_groups:
        lines.extend([
            '<table class="source-table"><thead><tr><th scope="col">来源任务 / 输入影像</th>',
            '<th scope="col">来源角色</th><th scope="col">运行状态</th>',
            '<th scope="col">保存结论</th></tr></thead><tbody>',
        ])
        for group in report.asset_groups:
            for source in group.sources:
                snapshot = source.task.snapshot
                lines.append(
                    f'<tr><td><a href="#source-{source.task_id}"><code>{source.task_id}</code></a>'
                    f'<br><code class="muted">{group.input_asset.asset_id} '
                    f"v{group.input_asset.version}</code></td>"
                    f"<td>{ROLES[source.role]}</td>"
                    f'<td><span class="status status--{snapshot.runtime_status.value}">'
                    f"{STATUSES[snapshot.runtime_status]}</span></td>"
                    f"<td>{RESULTS[snapshot.inspection_result]}</td></tr>"
                )
        lines.append("</tbody></table>")
    else:
        lines.append("<p>暂无已保存的检查任务；这不单独证明影像正常。</p>")
    lines.append('<h2 id="evidence">3. 检查结果与影像证据</h2>')
    for group_index, group in enumerate(report.asset_groups, 1):
        asset = group.input_asset
        lines.extend([
            '<section class="report-asset-group">',
            f"<h3>影像 {group_index} · <code>{asset.asset_id}</code> v{asset.version}</h3>",
            "<p>打开输入资产：" + evidence_link(asset) + "</p>",
        ])
        for source in group.sources:
            task = source.task
            snapshot = task.snapshot
            mode = ("纯 YOLO（未执行 VLM 复检）"
                    if task.inspection_mode == "yolo_only" else "YOLO＋VLM")
            lines.extend([
                f'<article class="report-source" id="source-{source.task_id}">',
                f"<h3>{ROLES[source.role]} · <code>{source.task_id}</code></h3>",
                metadata_table([
                    ("根任务", str(source.root_task_id),
                     "来源角色", f"{ROLES[source.role]} / {source.role}"),
                    ("提交时间", task.created_at.isoformat(),
                     "快照更新时间", snapshot.updated_at.isoformat()),
                    ("检测模式", mode, "输入版本", str(asset.version)),
                ]),
                "<h4>用户要求</h4><pre>" + escape(task.instruction) + "</pre>",
            ])
            lines.extend(render_task_summary(task))
            lines.extend(render_source_scope(task))
            if snapshot.is_terminal:
                lines.append(f'<p><a href="/api/tasks/{source.task_id}/report?format=html">'
                             "打开该来源的详细任务报告</a></p>")
            else:
                lines.append('<p class="muted">此来源尚未结束；详细任务报告在终态后可用。</p>')
            lines.append("<h4>已保存发现</h4>")
            lines.extend(render_findings(source))
            lines.extend(render_evidence_sections(source))
            lines.append("<h4>工具调用</h4>")
            lines.extend(render_tool_calls(source.tool_calls))
            lines.append("</article>")
        lines.append("</section>")
    lines.append('<h2 id="turns">4. 对话记录</h2>')
    for index, turn in enumerate(report.turns, 1):
        linked = f"；关联任务 {turn.linked_task_id}" if turn.linked_task_id else ""
        lines.extend([
            '<article class="report-turn">',
            f"<h3>轮次 {index}</h3>",
            f'<p class="muted"><code>{turn.request_id}</code> · '
            f"{_value(turn.status)} / {_value(turn.stage)}{linked}<br>"
            f"{escape(turn.created_at.isoformat())}</p>",
            f"<strong>用户</strong><pre>{escape(turn.text)}</pre>",
        ])
        if turn.reply:
            lines.append(f"<strong>助手回复</strong><pre>{escape(turn.reply)}</pre>")
        lines.append("</article>")
    if not report.turns:
        lines.append("<p>暂无已保存轮次。</p>")
    lines.extend(document_end())
    return "\n".join(lines)
