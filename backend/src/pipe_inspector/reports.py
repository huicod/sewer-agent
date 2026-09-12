"""Deterministic HTML view of stored evidence, with no model or tool invocation."""

from html import escape

from pipe_inspector.contracts.assets import AssetRef
from pipe_inspector.contracts.inspection import QualitySummary
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
ENHANCEMENTS = {
    "low_light": "低照度恢复",
    "dehaze": "去雾",
    "deblur": "去模糊",
    "super_resolution": "超分辨率",
}
DEGRADATIONS = {
    "low_light": "低照度",
    "mixed_dark_noise_blur": "暗部、噪声与模糊混合",
    "motion_blur": "运动模糊",
    "haze": "雾化",
    "small_detail": "细节过小",
    "none": "未发现明确退化",
    "unsupported": "当前工具不支持的退化",
}


def evidence_link(asset: AssetRef) -> str:
    path = f"/api/assets/{asset.asset_id}/versions/{asset.version}/content"
    return f'<a href="{path}">原始/派生证据 {asset.asset_id} v{asset.version}</a>'


def _asset_path(asset: AssetRef) -> str:
    return f"/api/assets/{asset.asset_id}/versions/{asset.version}/content"


def _quality(label: str, quality: QualitySummary) -> str:
    status = "充分" if quality.adequate else "不足"
    reasons = "、".join(escape(reason) for reason in quality.reasons) or "—"
    return (
        f"<h4>{label}</h4><p>亮度 {quality.mean_luminance:g}；"
        f"对比度 {quality.contrast:g}；清晰度 {quality.sharpness:g}；"
        f"质量{status}；说明 {reasons}</p>"
    )


def _enhancement_section(report: TaskReport) -> list[str]:
    selection = report.enhancement_selection
    result = report.enhancement
    if selection is None and result is None:
        return []
    lines = [
        '<section class="enhancement"><h2>图片增强对照</h2>',
        "<p>增强图是从原始输入派生的辅助证据；派生图仅供辅助复核，不改写原检查结论。</p>",
    ]
    if selection is not None:
        if selection.status == "selected":
            kind = ENHANCEMENTS[selection.options.kind.value]
            lines.append(
                f"<p>自动增强已选择：{kind}；理由：{escape(selection.reason)}</p>"
            )
        else:
            lines.append(f"<p>自动增强已跳过；理由：{escape(selection.reason)}</p>")
        if selection.proposal is not None:
            proposal = selection.proposal
            confidence = "明确" if proposal.confidence == "clear" else "不确定"
            lines.append(
                f"<p>退化判断：{DEGRADATIONS[proposal.degradation]}（{confidence}）；"
                f"证据：{escape(proposal.evidence)}；对检查判读的影响："
                f"{escape(proposal.inspection_impact)}</p>"
            )
    if result is None:
        if selection is not None and selection.status == "selected":
            lines.append("<p>增强已选择，但没有已保存的增强结果。</p>")
        lines.append("</section>")
        return lines
    source_path = _asset_path(result.input_asset)
    output_path = _asset_path(result.output_asset.ref)
    roi = ", ".join(f"{coordinate:g}" for coordinate in result.source_region.bbox_xyxy)
    lines.extend(
        [
            '<div class="image-comparison">',
            f'<figure><img src="{source_path}" alt="原始输入"><figcaption>'
            f'<a href="{source_path}">打开原始输入</a></figcaption></figure>',
            f'<figure><img src="{output_path}" alt="增强派生图"><figcaption>'
            f'<a href="{output_path}">打开增强派生图</a></figcaption></figure>',
            "</div>",
            f"<p>原图处理范围：{result.source_region.width} × {result.source_region.height} 像素；"
            f"ROI（原图坐标）：({roi})</p>",
            f"<p>增强类型：{ENHANCEMENTS[result.kind.value]}；模型：{escape(result.model_id)} "
            f"v{escape(result.model_version)}</p>",
            _quality("增强前质量", result.quality_before),
            _quality("增强后质量", result.quality_after),
        ]
    )
    if report.enhancement_review is None:
        lines.append("<p>辅助复核尚未生成。</p>")
    else:
        review = report.enhancement_review
        lines.extend(
            [
                "<h3>增强辅助复核</h3>",
                f"<p>质量变化：{escape(review.quality_effect)}；"
                f"冲突发现 {len(review.conflicting_finding_ids)} 项；"
                f"新增疑似异常 {len(review.additional_anomalies)} 项。</p>",
                "<p>复核摘要：" + escape(review.summary) + "</p>",
            ]
        )
    lines.append("</section>")
    return lines


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
        "pre{white-space:pre-wrap;overflow-wrap:anywhere}li{margin:.5rem 0}"
        ".image-comparison{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:1rem}"
        ".image-comparison figure{margin:0}"
        ".image-comparison img{display:block;width:100%;height:auto}"
        "</style></head><body>",
        "<h1>管网检查报告</h1>",
        f"<p>任务：{snapshot.task_id}</p>",
        f"<p>结果：{RESULTS[snapshot.inspection_result]}"
        f"；运行状态：{snapshot.runtime_status.value}</p>",
        f"<p>快照更新时间：{snapshot.updated_at.isoformat()}</p>",
        "<h2>用户要求</h2><pre>" + escape(task.instruction) + "</pre>",
        "<p>" + evidence_link(task.input_asset) + "</p>",
        f"<h2>证据覆盖</h2><p>{covered}；{quality}；{audited}</p>",
    ]
    if task.parent_task_id:
        lines.append(
            '<p>关联追加复检：<a href="/api/tasks/'
            f'{task.parent_task_id}/report?format=html">原任务报告</a>'
            "。复用原图片及原检测证据，未重新运行检测器；原结果未改写。"
            "原检测器版本和调用记录见原任务报告。</p>"
        )
    if snapshot.waiting_reason:
        lines.append("<p>限制/待处理：" + escape(snapshot.waiting_reason) + "</p>")
    lines.extend(_enhancement_section(report))
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
