"""Deterministic HTML view of stored evidence, with no model or tool invocation."""

from html import escape

from pipe_inspector.contracts.assets import AssetRef
from pipe_inspector.contracts.inspection import QualitySummary
from pipe_inspector.contracts.intake import TaskRecord
from pipe_inspector.contracts.reports import SessionReportSource, TaskReport
from pipe_inspector.contracts.tools import ToolCall
from pipe_inspector.report_document import document_end, document_start, metadata_table

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
STATUSES = {
    "queued": "等待启动",
    "running": "检查中",
    "waiting_review": "待人工复核",
    "completed": "已完成",
    "partial": "部分完成",
    "failed": "处理失败",
    "cancelled": "已取消",
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


def _coexisting_text(value: object) -> str:
    labels = getattr(value, "coexisting_labels", ())
    if not labels:
        return ""
    rendered = "、".join(
        escape(label.value if hasattr(label, "value") else str(label)) for label in labels
    )
    return f"；共存建议 {rendered}"


def _original_presence_text(value: object) -> str:
    presence = getattr(value, "original_presence", None)
    if presence is None:
        return ""
    rendered = {
        "present": "原类别存在",
        "absent": "原类别未见",
        "uncertain": "原类别不确定",
    }.get(str(presence), str(presence))
    return f"；原类别判断 {escape(rendered)}"


def _confirmation_text(value: object) -> str:
    status = getattr(value, "confirmation_status", None)
    if status is None:
        return ""
    rendered = {
        "confirmed": "已完成独立回看",
        "uncertain": "尚未完成独立回看",
    }.get(str(status), str(status))
    return f"；新增二次确认 {escape(rendered)}"


def _image_evidence_section(report: TaskReport | SessionReportSource) -> list[str]:
    detail = report.image_evidence
    if detail is None or (detail.observation is None and detail.review is None):
        return []
    lines = ["<h2>图片检测与复核溯源</h2>", "<h3>YOLO / 主检测器原始结果</h3>"]
    if detail.review_status == "skipped_by_mode":
        lines.append("<p>检测模式：纯 YOLO；按用户选择未执行 VLM 复检。</p>")
    if detail.observation is not None:
        detection = detail.observation.detection
        lines.append(
            f"<p>检测器：{escape(detection.detector_id)}；"
            f"模型：{escape(detection.model_version)}；"
            f"原图 {detection.width}×{detection.height}</p>"
        )
        for index, item in enumerate(detection.detections):
            lines.append(
                f"<p>候选 #{index}：{item.label.value}；原检测分数 {item.score:.4f}；"
                f"原图像素框 {item.bbox_xyxy}</p>"
            )
        if not detection.detections:
            lines.append("<p>主检测器零候选，不等于整图正常。</p>")
    lines.append("<h3>VLM 复核意见</h3>")
    if detail.review is None:
        lines.append(
            "<p>纯 YOLO 模式已跳过 VLM 复检；这不是 VLM 缺失或调用失败。</p>"
            if detail.review_status == "skipped_by_mode"
            else "<p>本任务没有已保存的图片 VLM 复核意见。</p>"
        )
    else:
        review = detail.review
        lines.append(f"<p>{escape(review.summary)}</p>")
        lines.append(
            f"<p>可判读：{'是' if review.image_assessable else '否'}；"
            f"全图审计完成：{'是' if review.full_image_audited else '否'}</p>"
        )
        for item in review.candidates:
            proposed = item.proposed_label.value if item.proposed_label else "—"
            lines.append(
                f"<p>候选 #{item.index}：{item.verdict}；建议类别 {proposed}；"
                f"证据 {item.evidence_strength or '旧记录未提供'}"
                f"{_original_presence_text(item)}{_coexisting_text(item)}；{escape(item.reason)}</p>"
            )
        for index, item in enumerate(review.additional_anomalies):
            lines.append(
                f"<p>补充 #{index}：{escape(str(item.label))}；{escape(item.name or '')}；"
                f"新增证据强度：{item.evidence_strength or '旧记录未提供'}；"
                f"{_confirmation_text(item)}；"
                f"原图像素框 {item.bbox_xyxy}；{escape(item.reason)}；无数值置信度</p>"
            )
    lines.append("<h3>已保存的融合动作</h3>")
    for item in detail.fusion_changes:
        origin = (
            f"候选 #{item.index}" if item.index is not None else f"补充 #{item.additional_index}"
        )
        lines.append(
            f"<p>{origin}：{escape(item.action)} → {escape(str(item.label))}；"
            f"融合状态：{'待人工处理' if item.pending else '已自动处理'}"
            f"{_original_presence_text(item)}{_confirmation_text(item)}"
            f"{_coexisting_text(item)}；{escape(item.reason)}；{escape(item.gate or '')}</p>"
        )
    if not detail.fusion_changes:
        lines.append("<p>没有已保存的逐项融合动作，不重新推算历史结果。</p>")
    lines.append("<p>以上是原始复核与融合记录；当前状态（含人工审核）以本报告发现项为准。</p>")
    return lines


def _enhancement_section(report: TaskReport | SessionReportSource) -> list[str]:
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
            lines.append(f"<p>自动增强已选择：{kind}；理由：{escape(selection.reason)}</p>")
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


def _geometry_section(report: TaskReport | SessionReportSource) -> list[str]:
    detail = report.geometry
    if detail is None:
        return []
    segmentation = detail.segmentation
    depth = detail.depth
    measurements = detail.measurements
    lines = [
        '<section class="geometry"><h2>图像几何证据</h2>',
        "<p>结果保持在原图像素网格；像素统计和单图相对深度不是实际物理尺寸。</p>",
    ]
    if segmentation is None and depth is None:
        lines.extend(
            ["<p>几何工具没有已保存的成功结果；失败信息见工具调用记录。</p>", "</section>"]
        )
        return lines
    source_path = _asset_path(report.task.input_asset)
    lines.append(
        f'<figure><img src="{source_path}" alt="几何工具原图"><figcaption>'
        f'<a href="{source_path}">打开几何工具原图</a></figcaption></figure>'
    )
    if segmentation is not None:
        prompt = ", ".join(f"{value:g}" for value in segmentation.prompt.bbox_xyxy)
        mask_path = _asset_path(segmentation.mask_asset.ref)
        overlay_path = _asset_path(segmentation.overlay_asset.ref)
        lines.extend(
            [
                "<h3>提示式掩膜</h3>",
                f"<p>原图提示框坐标：({prompt})；提示框不是掩膜真值。</p>",
                '<div class="image-comparison">',
                f'<figure><img src="{mask_path}" alt="二值掩膜"><figcaption>'
                f'<a href="{mask_path}">打开二值掩膜</a></figcaption></figure>',
                f'<figure><img src="{overlay_path}" alt="掩膜叠加图"><figcaption>'
                f'<a href="{overlay_path}">打开掩膜叠加图</a></figcaption></figure>',
                "</div>",
                f"<p>模型：{escape(segmentation.model_id)} v{escape(segmentation.model_version)}；"
                f"掩膜质量估计 {segmentation.model_score:g}，不是缺陷置信度。</p>",
            ]
        )
    if depth is not None:
        preview_path = _asset_path(depth.preview_asset.ref)
        raw_path = _asset_path(depth.raw_asset.ref)
        lines.extend(
            [
                "<h3>单图相对深度</h3>",
                f'<figure><img src="{preview_path}" alt="相对深度预览"><figcaption>'
                f'<a href="{preview_path}">打开深度预览</a></figcaption></figure>',
                f'<p><a href="{raw_path}">下载 float32 NPY 相对深度数组</a></p>',
                "<p>预览图例：白色=近，黑色=远；数值是相对模型单位，不能跨图比较。</p>",
                f"<p>模型：{escape(depth.model_id)} v{escape(depth.model_version)}；"
                f"本图原始范围 {depth.raw_min:g}–{depth.raw_max:g}。</p>",
            ]
        )
    if measurements is not None:
        fraction = measurements.image_plane_fraction * 100
        lines.extend(
            [
                "<h3>图像平面量化</h3>",
                f"<p>掩膜面积 {measurements.area_pixels} 像素；占全图像素 {fraction:g}%；"
                f"掩膜边界框 {measurements.bbox_width_pixels:g} × "
                f"{measurements.bbox_height_pixels:g} 像素。</p>",
            ]
        )
        if measurements.relative_depth is not None:
            summary = measurements.relative_depth
            lines.append(
                f"<p>掩膜内相对深度：q10 {summary.q10:g}；中位数 {summary.median:g}；"
                f"q90 {summary.q90:g}；相对模型单位，不能跨图比较。</p>"
            )
    lines.append("</section>")
    return lines


def render_evidence_sections(report: TaskReport | SessionReportSource) -> list[str]:
    """Render stored enhancement, geometry, and detector/reviewer evidence fragments."""
    lines: list[str] = []
    lines.extend(_enhancement_section(report))
    lines.extend(_geometry_section(report))
    lines.extend(_image_evidence_section(report))
    return lines


def render_task_summary(task: TaskRecord) -> list[str]:
    """Render persisted task state without deriving a new conclusion."""
    snapshot = task.snapshot
    coverage = snapshot.coverage
    quality = "画质充分" if coverage.sufficient_quality else "画质不足"
    covered = "覆盖充分" if coverage.sufficient_coverage else "覆盖不足"
    audited = "已完成阴性审计" if coverage.negative_audit_complete else "阴性审计未完成"
    lines = [
        metadata_table([
            ("检查结论", RESULTS[snapshot.inspection_result], "运行状态",
             f"{STATUSES[snapshot.runtime_status]} / {snapshot.runtime_status.value}"),
            ("证据覆盖", covered, "影像质量", quality),
            ("阴性审计", audited, "总进度", f"{snapshot.overall_progress:.0%}"),
            ("处理帧 / 块", f"{snapshot.processed_frames} / {snapshot.processed_chunks}",
             "阶段", snapshot.stage.value),
        ]),
    ]
    if snapshot.waiting_reason:
        lines.append('<p class="report-note report-note--pending">限制/待处理：'
                     + escape(snapshot.waiting_reason) + "</p>")
    if not snapshot.is_terminal:
        lines.append('<p class="report-note report-note--pending">此来源尚未结束，'
                     "当前状态与证据可能继续更新。</p>")
    return lines


def render_source_scope(task: TaskRecord) -> list[str]:
    """Keep original-image and selected-frame scopes explicit in either report."""
    lines: list[str] = []
    if task.frame_source is not None:
        source = task.frame_source
        lines.append(
            '<p class="report-note">视频帧工具：<a href="/api/tasks/'
            f'{task.parent_task_id}/report?format=html">原视频任务报告</a>'
            f"；帧 {source.frame_index}；原视频时间 {source.timestamp_seconds:g} 秒。"
            "本报告结果与覆盖仅针对所选视频帧，整段视频结论不变；"
            "复用已保存帧和检测/复检证据，未重新扫描视频。</p>"
        )
        lines.append("<p>源视频：" + evidence_link(source.video_asset) + "</p>")
    elif task.parent_task_id:
        lines.append(
            '<p class="report-note">关联追加复检：<a href="/api/tasks/'
            f'{task.parent_task_id}/report?format=html">原任务报告</a>'
            "。复用原图片及原检测证据，未重新运行检测器；原结果未改写。"
            "原检测器版本和调用记录见原任务报告。</p>"
        )
    return lines


def render_findings(report: TaskReport | SessionReportSource) -> list[str]:
    """Show each stored finding and preview only references known to be image evidence."""
    task = report.task
    findings = task.snapshot.findings
    if not findings:
        return ["<p>没有已保存的缺陷发现；是否正常以任务结论和覆盖条件为准。</p>"]
    lines = [
        '<table class="findings-table"><thead><tr><th scope="col">序号</th>',
        '<th scope="col">缺陷类别 / 发现 ID</th><th scope="col">当前状态</th>',
        '<th scope="col">候选来源与依据</th><th scope="col">影像证据</th></tr></thead><tbody>',
    ]
    known_input_image = bool(task.frame_source or report.image_evidence or report.enhancement
                             or report.geometry)
    # Preserve exact AssetRef identity. Multiple findings on one image share its preview.
    previews: dict[tuple[object, int], tuple[AssetRef, list[str]]] = {}
    for index, finding in enumerate(findings, 1):
        label = LABELS.get(finding.canonical_label, "待定类别")
        lines.extend(
            [
                f"<tr><td>{index}</td><td><strong>{label}</strong>",
                f'<code class="finding-id">{finding.finding_id}</code>',
            ]
        )
        if finding.open_label:
            lines.append("<p>候选名称：" + escape(finding.open_label) + "</p>")
        lines.append(
            f'</td><td><span class="status status--{finding.review_status.value}">'
            f"{REVIEWS[finding.review_status]}</span></td>"
            f"<td>候选来源：{finding.source.value}"
        )
        if finding.score is not None:
            lines.append(
                f"<p>原检测候选分数：{finding.score:.4f}"
                "（非准确率，不是融合类别置信度）</p>"
            )
        if finding.confirmed_by:
            confirmed_by = {
                "detector_only_rules": "纯 YOLO 检测器规则（非 VLM 确认）",
                "rules": "融合规则",
                "human": "人工审核",
            }[finding.confirmed_by]
            lines.append("<p>确认依据：" + confirmed_by + "</p>")
        lines.append('</td><td><ul class="evidence-links">')
        for evidence in finding.evidence:
            lines.append("<li>" + evidence_link(evidence.asset))
            locator = f"发现 {index}：{label}"
            if evidence.frame_index is not None:
                locator += (f" · 帧 {evidence.frame_index} · "
                            f"{evidence.timestamp_seconds:g} 秒")
                lines.append(
                    f'<p class="muted">帧 {evidence.frame_index}；'
                    f"原视频时间 {evidence.timestamp_seconds:g} 秒</p>"
                )
            if evidence.region:
                lines.append('<p class="muted">原图像素坐标：'
                             + escape(str(evidence.region.bbox_xyxy)) + "</p>")
            # Legacy locators can point to the input video itself, not an image.
            # A frame-tool task explicitly proves that its own input is a saved frame.
            original_video_locator = (
                evidence.frame_index is not None
                and evidence.asset == task.input_asset
                and task.frame_source is None
            )
            if not original_video_locator and (
                evidence.region is not None or evidence.frame_index is not None
                or (known_input_image and evidence.asset == task.input_asset)
            ):
                key = (evidence.asset.asset_id, evidence.asset.version)
                _, captions = previews.setdefault(key, (evidence.asset, []))
                if locator not in captions:
                    captions.append(locator)
            lines.append("</li>")
        lines.append("</ul></td></tr>")
    lines.append("</tbody></table>")
    if previews:
        lines.append('<h3>影像证据</h3><div class="evidence-gallery">')
        for asset, captions in previews.values():
            path = _asset_path(asset)
            caption = escape("；".join(captions))
            lines.append(
                f'<figure><a href="{path}"><img src="{path}" '
                f'alt="{caption}" loading="eager"></a>'
                f'<figcaption>{caption}<br><a href="{path}">打开原始证据 '
                f"v{asset.version}</a></figcaption></figure>"
            )
        lines.append("</div>")
    return lines


def render_tool_calls(calls: tuple[ToolCall, ...]) -> list[str]:
    if not calls:
        return ["<p>暂无工具调用记录。</p>"]
    lines = [
        '<table class="tool-table"><thead><tr><th scope="col">工具 / 模型</th>',
        '<th scope="col">执行状态</th><th scope="col">说明</th></tr></thead><tbody>',
    ]
    for call in calls:
        detail = "—"
        if call.error:
            detail = f"{call.error.code}：{call.error.message}"
        if call.skip_reason:
            detail = f"{detail}；跳过原因：{call.skip_reason}" if call.error else (
                f"跳过原因：{call.skip_reason}"
            )
        lines.append(
            f"<tr><td>{escape(call.tool_name.value)} v{escape(call.tool_version)}"
            f'<br><span class="muted">模型 {escape(call.model_version or "—")}</span></td>'
            f"<td>{escape(call.status.value)}<br>尝试 {call.attempt}</td>"
            f"<td>{escape(detail)}</td></tr>"
        )
    lines.append("</tbody></table>")
    return lines


def render_html(report: TaskReport) -> str:
    task, snapshot = report.task, report.task.snapshot
    mode = "纯 YOLO（未执行 VLM 复检）" if task.inspection_mode == "yolo_only" else "YOLO＋VLM"
    lines = document_start("管道影像检查报告", "单任务报告 · 已保存检查结果与影像证据")
    lines.extend([
        '<nav class="report-outline" aria-label="报告目录">'
        '<a href="#basic">基本信息</a><a href="#summary">检查摘要</a>'
        '<a href="#findings">缺陷候选结果</a><a href="#tools">工具调用记录</a></nav>',
        '<h2 id="basic">1. 基本信息</h2>',
        metadata_table([
            ("任务编号", str(snapshot.task_id), "会话编号", str(task.session_id)),
            ("提交时间", task.created_at.isoformat(),
             "快照更新时间", snapshot.updated_at.isoformat()),
            ("检测模式", mode, "输入版本", str(task.input_asset.version)),
        ]),
        "<h3>用户要求</h3><pre>" + escape(task.instruction) + "</pre>",
        "<p>输入文件：" + evidence_link(task.input_asset) + "</p>",
        '<h2 id="summary">2. 检查摘要与证据覆盖</h2>',
    ])
    lines.extend(render_task_summary(task))
    lines.extend(render_source_scope(task))
    lines.append('<h2 id="findings">3. 缺陷候选结果</h2>')
    lines.extend(render_findings(report))
    lines.extend(render_evidence_sections(report))
    lines.append('<h2 id="tools">4. 工具调用记录</h2>')
    lines.extend(render_tool_calls(report.tool_calls))
    lines.extend(document_end())
    return "\n".join(lines)
