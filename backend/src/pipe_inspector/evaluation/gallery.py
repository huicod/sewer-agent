"""Deterministic static evidence gallery for fixed image evaluations."""

from __future__ import annotations

import base64
import json
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any

from pipe_inspector.agent.guided_review import GuidedReview
from pipe_inspector.contracts.inspection import ImageObservation, ImageReview

from .review import LABELS, EvaluationManifest, EvaluationSample, LabeledBox, score_boxes

_IMAGE_SUFFIXES = {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
_IMAGE_MIME_TYPES = {
    ".bmp": "image/bmp",
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".webp": "image/webp",
}
_LAYER_COLOURS = {
    "yolo": "#22d3ee",
    "suggestions": "#c084fc",
    "fused": "#fb923c",
    "ground-truth": "#4ade80",
}


@dataclass(frozen=True)
class _PreparedRecord:
    observation: ImageObservation | None
    predictions: tuple[LabeledBox, ...] | None
    review: GuidedReview | ImageReview | None
    review_error: str | None
    pending: bool
    changes: tuple[Mapping[str, Any], ...]
    issues: tuple[str, ...]


@dataclass(frozen=True)
class _SuggestionBox:
    label: str
    bbox_xyxy: tuple[float, float, float, float]
    display_index: int
    score: None = None


def _html(value: object) -> str:
    return escape(str(value), quote=True)


def _number(value: float | int) -> str:
    number = float(value)
    if number == 0:
        return "0"
    if number.is_integer():
        return str(int(number))
    return format(number, ".12g")


def _write_text(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8")


def _prepare_record(record: Mapping[str, Any]) -> _PreparedRecord:
    issues: list[str] = []
    observation = None
    predictions = None
    review = None

    try:
        observation = ImageObservation.model_validate(record["observation"])
    except (KeyError, TypeError, ValueError):
        issues.append("observation 无效")

    try:
        raw_predictions = record["predictions"]
        if isinstance(raw_predictions, (str, bytes)) or not isinstance(raw_predictions, Sequence):
            raise TypeError("predictions must be a list")
        predictions = tuple(LabeledBox.model_validate(item) for item in raw_predictions)
    except (KeyError, TypeError, ValueError):
        issues.append("predictions 无效")

    raw_review = record.get("review")
    if raw_review is not None:
        try:
            review = GuidedReview.model_validate(raw_review)
        except (TypeError, ValueError):
            try:
                review = ImageReview.model_validate(raw_review)
            except (TypeError, ValueError):
                issues.append("review 无效")

    raw_changes = record.get("changes", ())
    if isinstance(raw_changes, Sequence) and not isinstance(raw_changes, (str, bytes)):
        changes = tuple(item for item in raw_changes if isinstance(item, Mapping))
        if len(changes) != len(raw_changes):
            issues.append("changes 含无效项目")
    else:
        changes = ()
        issues.append("changes 无效")

    raw_error = record.get("review_error")
    review_error = None if raw_error is None else str(raw_error)
    return _PreparedRecord(
        observation=observation,
        predictions=predictions,
        review=review,
        review_error=review_error,
        pending=record.get("pending") is True,
        changes=changes,
        issues=tuple(issues),
    )


def _detector_boxes(observation: ImageObservation | None) -> tuple[LabeledBox, ...] | None:
    if observation is None:
        return None
    return tuple(
        LabeledBox(label=item.label, bbox_xyxy=item.bbox_xyxy, score=item.score)
        for item in observation.detection.detections
    )


def _suggestion_boxes(
    review: GuidedReview | ImageReview | None,
) -> tuple[_SuggestionBox, ...] | None:
    if review is None:
        return None
    boxes = []
    for index, anomaly in enumerate(review.additional_anomalies):
        label = str(anomaly.label)
        if label not in LABELS or anomaly.bbox_xyxy is None:
            continue
        boxes.append(
            _SuggestionBox(
                label=label,
                bbox_xyxy=tuple(float(value) for value in anomaly.bbox_xyxy),
                display_index=index,
            )
        )
    return tuple(boxes)


def _placeholder_svg(sample: EvaluationSample, message: str) -> str:
    width, height = sample.width, sample.height
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">\n'
        '  <rect width="100%" height="100%" fill="#111827"/>\n'
        f'  <text x="50%" y="50%" text-anchor="middle" dominant-baseline="middle" '
        f'fill="#fca5a5" font-size="14">{_html(message)}</text>\n'
        "</svg>\n"
    )


def _overlay_svg(
    sample: EvaluationSample,
    *,
    image_href: str,
    boxes: Sequence[LabeledBox | _SuggestionBox] | None,
    layer: str,
    unavailable: str | None = None,
) -> str:
    width, height = sample.width, sample.height
    colour = _LAYER_COLOURS[layer]
    title = {
        "yolo": "YOLO",
        "suggestions": "VLM 新增建议（融合前，非最终预测）",
        "fused": "融合预测",
        "ground-truth": "真实标注",
    }[layer]
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}">'
        ),
        f"  <title>{_html(sample.sample_id)} · {_html(title)}</title>",
        (
            f'  <image href="{_html(image_href)}" x="0" y="0" width="{width}" '
            f'height="{height}" preserveAspectRatio="none"/>'
        ),
    ]
    if boxes is not None:
        for index, box in enumerate(boxes):
            x1, y1, x2, y2 = box.bbox_xyxy
            label = box.label.value if hasattr(box.label, "value") else str(box.label)
            display_index = getattr(box, "display_index", index)
            score = box.score
            score_attribute = "" if score is None else f' data-score="{_number(score)}"'
            # Fusion scores are matching-order values, not calibrated VLM probabilities.
            score_text = f" {score:.3f}" if score is not None and layer == "yolo" else ""
            label_y = min(max(14.0, y1 - 3), max(0.0, height - 2))
            lines.extend(
                [
                    (
                        f'  <rect data-box-index="{display_index}" data-label="{_html(label)}"'
                        f'{score_attribute} x="{_number(x1)}" '
                        f'y="{_number(y1)}" width="{_number(x2 - x1)}" '
                        f'height="{_number(y2 - y1)}" fill="none" stroke="{colour}" '
                        'stroke-width="2" vector-effect="non-scaling-stroke"/>'
                    ),
                    (
                        f'  <text data-box-index="{display_index}" x="{_number(x1)}" '
                        f'y="{_number(label_y)}" '
                        f'fill="{colour}" font-size="12" font-weight="700" '
                        f'stroke="#111827" stroke-width="3" paint-order="stroke">'
                        f"{_html(f'#{display_index} {label}{score_text}')}</text>"
                    ),
                ]
            )
    banner = unavailable
    if banner is None and boxes is not None and not boxes:
        banner = "0 个框"
    if banner is not None:
        lines.extend(
            [
                f'  <rect x="0" y="0" width="{width}" height="24" fill="#7f1d1d" opacity="0.9"/>',
                f'  <text x="8" y="17" fill="#fff" font-size="13">{_html(banner)}</text>',
            ]
        )
    lines.append("</svg>")
    return "\n".join(lines) + "\n"


def _counts(
    targets: Sequence[LabeledBox], boxes: Sequence[LabeledBox] | None
) -> Mapping[str, Any] | None:
    return None if boxes is None else score_boxes(targets, boxes)["micro"]


def _metric_markup(stage: str, title: str, counts: Mapping[str, Any] | None) -> str:
    if counts is None:
        return f'<div class="metric unavailable" data-stage="{stage}">{title}：不可计算</div>'
    return (
        f'<div class="metric" data-stage="{stage}" data-tp="{counts["tp"]}" '
        f'data-fp="{counts["fp"]}" data-fn="{counts["fn"]}">'
        f"{title}：TP {counts['tp']} / FP {counts['fp']} / FN {counts['fn']}</div>"
    )


def _status_messages(prepared: _PreparedRecord | None, *, source_available: bool) -> list[str]:
    messages = []
    if prepared is None:
        messages.append("记录缺失")
    else:
        if prepared.review_error is not None:
            messages.append(f"复核失败：{prepared.review_error}")
        if prepared.pending:
            messages.append("图像级待审")
        if any(bool(change.get("pending")) for change in prepared.changes):
            messages.append("存在待审改动")
        messages.extend(prepared.issues)
    if not source_available:
        messages.append("原图缺失")
    if not messages:
        messages.append("记录完整")
    return messages


def _changes_markup(changes: Sequence[Mapping[str, Any]]) -> str:
    if not changes:
        return '<p class="muted">无改动记录</p>'
    items = []
    for change in changes:
        bits = [f"动作：{change.get('action', '未说明')}"]
        if "index" in change:
            bits.append(f"候选：{change['index']}")
        if "additional_index" in change:
            bits.append(f"新增序号：{change['additional_index']}")
        if change.get("label") is not None:
            bits.append(f"类别：{change['label']}")
        if change.get("gate") is not None:
            bits.append(f"门控：{change['gate']}")
        if change.get("bbox_xyxy") is not None:
            bits.append(
                "框：" + json.dumps(change["bbox_xyxy"], ensure_ascii=False, separators=(",", ":"))
            )
        if change.get("pending"):
            bits.append("待审")
        reason = change.get("reason", "未说明原因")
        items.append(f"<li>{_html('；'.join(str(bit) for bit in bits))}<br>{_html(reason)}</li>")
    return "<ol>" + "".join(items) + "</ol>"


def _review_markup(review: GuidedReview | ImageReview | None) -> str:
    if review is None:
        return '<p class="muted">无可用模型复核结果</p>'
    items = [f"<li>总结：{_html(review.summary)}</li>"]
    items.append(f"<li>全图审计：{'已完成' if review.full_image_audited else '未完成'}</li>")
    items.extend(
        f"<li>候选 {item.index} · {item.verdict}：{_html(item.reason)}</li>"
        for item in review.candidates
    )
    for item in review.additional_anomalies:
        note = ""
        if str(item.label) == "OTHER":
            note = "（未绘制：OTHER 不属于五类）"
        elif item.bbox_xyxy is None:
            note = "（未绘制：未定位）"
        items.append(f"<li>新增 {item.label}{note}：{_html(item.reason)}</li>")
    return "<ul>" + "".join(items) + "</ul>"


def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_html(title)}</title>
  <style>
    :root {{ color-scheme: dark; font-family: system-ui, sans-serif; }}
    body {{ margin: 0 auto; max-width: 1500px; padding: 24px;
            background: #0b1120; color: #e5e7eb; }}
    a {{ color: #67e8f9; }} .muted {{ color: #9ca3af; }}
    .status {{ padding: 10px 12px; margin: 12px 0; border-left: 4px solid #f59e0b;
               background: #1f2937; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
             gap: 16px; }}
    .panel {{ padding: 12px; background: #111827; border: 1px solid #374151; border-radius: 8px; }}
    .panel img {{ width: 100%; height: auto; display: block; background: #000; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ padding: 9px; border-bottom: 1px solid #374151; text-align: left; }}
    .metric {{ display: inline-block; margin: 6px 8px 6px 0; padding: 8px 10px;
               background: #1f2937; border-radius: 6px; }}
    code {{ overflow-wrap: anywhere; }}
  </style>
</head>
<body>{body}</body>
</html>
"""


def _detail_page(
    sample: EvaluationSample,
    prepared: _PreparedRecord | None,
    *,
    image_name: str,
    source_available: bool,
    raw_record_available: bool,
) -> str:
    detector_boxes = _detector_boxes(prepared.observation) if prepared is not None else None
    predictions = prepared.predictions if prepared is not None else None
    targets = sample.ground_truth
    statuses = _status_messages(prepared, source_available=source_available)
    raw_link = ""
    if raw_record_available:
        raw_link = (
            ' · <a href="review.json">VLM 建议（像素坐标）</a>'
            ' · <a href="fusion.json">采纳融合 JSON</a>'
            ' · <a href="record.json">完整记录 JSON</a>'
        )
    status_markup = "".join(f'<div class="status">{_html(item)}</div>' for item in statuses)
    panels = [
        ("原图", image_name),
        ("YOLO", "yolo.svg"),
        ("VLM 新增建议（融合前，非最终预测）", "vlm-suggestions.svg"),
        ("融合预测", "fused.svg"),
        ("真实标注", "ground-truth.svg"),
    ]
    panel_markup = "".join(
        f'<section class="panel"><h2>{title}</h2><a href="{name}" download>下载</a>'
        f'<img src="{name}" alt="{title}"></section>'
        for title, name in panels
    )
    changes = prepared.changes if prepared is not None else ()
    review = prepared.review if prepared is not None else None
    detector_metrics = _metric_markup("yolo", "YOLO", _counts(targets, detector_boxes))
    fused_metrics = _metric_markup("fused", "融合", _counts(targets, predictions))
    body = f"""
<p><a href="../index.html">← 返回总览</a>{raw_link}</p>
<h1>{_html(sample.sample_id)}</h1>
<p class="muted">组：{_html(sample.group_id)} · 原始尺寸：{sample.width} × {sample.height}</p>
{status_markup}
<div>{detector_metrics}{fused_metrics}</div>
<div class="grid">{panel_markup}</div>
<section><h2>候选改动</h2>{_changes_markup(changes)}</section>
<section><h2>模型理由</h2>{_review_markup(review)}</section>
"""
    return _page(f"逐图证据 · {sample.sample_id}", body)


def _data_uri(data: bytes, mime_type: str) -> str:
    return f"data:{mime_type};base64,{base64.b64encode(data).decode('ascii')}"


def _copy_original(sample: EvaluationSample, sample_dir: Path) -> tuple[str, str, bool]:
    suffix = sample.image_path.suffix.lower()
    suffix = suffix if suffix in _IMAGE_SUFFIXES else ".img"
    image_name = f"original{suffix}"
    if sample.image_path.is_file():
        destination = sample_dir / image_name
        shutil.copyfile(sample.image_path, destination)
        mime_type = _IMAGE_MIME_TYPES.get(suffix, "application/octet-stream")
        return image_name, _data_uri(destination.read_bytes(), mime_type), True
    placeholder = "original-unavailable.svg"
    placeholder_bytes = _placeholder_svg(sample, "原图缺失").encode()
    (sample_dir / placeholder).write_bytes(placeholder_bytes)
    return placeholder, _data_uri(placeholder_bytes, "image/svg+xml"), False


def export_gallery(
    manifest: EvaluationManifest,
    records: list[dict],
    output_dir: Path,
) -> Path:
    """Export every manifest sample as a self-contained static evidence directory."""
    output_dir.mkdir(parents=True, exist_ok=True)
    records_by_id: dict[str, Mapping[str, Any]] = {}
    expected_ids = {sample.sample_id for sample in manifest.samples}
    for record in records:
        sample_id = str(record.get("sample_id", ""))
        if sample_id in records_by_id:
            raise ValueError(f"duplicate gallery record for sample_id: {sample_id}")
        if sample_id not in expected_ids:
            raise ValueError(f"gallery record is not present in manifest: {sample_id}")
        records_by_id[sample_id] = record

    rows = []
    recorded = 0
    for position, sample in enumerate(manifest.samples, start=1):
        sample_dir_name = f"sample-{position:04d}"
        sample_dir = output_dir / sample_dir_name
        sample_dir.mkdir(parents=True, exist_ok=True)
        image_name, image_data_uri, source_available = _copy_original(sample, sample_dir)
        record = records_by_id.get(sample.sample_id)
        prepared = _prepare_record(record) if record is not None else None
        if record is not None:
            recorded += 1
            _write_text(
                sample_dir / "record.json",
                json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            )
            _write_text(
                sample_dir / "review.json",
                json.dumps(record.get("review"), ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
            )
            fusion_record = {
                key: record.get(key)
                for key in ("predictions", "changes", "review_error", "pending")
                if key in record
            }
            _write_text(
                sample_dir / "fusion.json",
                json.dumps(fusion_record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            )

        detector_boxes = _detector_boxes(prepared.observation) if prepared is not None else None
        suggestions = _suggestion_boxes(prepared.review) if prepared is not None else None
        predictions = prepared.predictions if prepared is not None else None
        unavailable = "记录缺失" if prepared is None else None
        if prepared is not None and prepared.observation is None:
            unavailable = "observation 无效"
        _write_text(
            sample_dir / "yolo.svg",
            _overlay_svg(
                sample,
                image_href=image_data_uri,
                boxes=detector_boxes,
                layer="yolo",
                unavailable=unavailable,
            ),
        )
        fused_unavailable = "记录缺失" if prepared is None else None
        if prepared is not None and prepared.predictions is None:
            fused_unavailable = "predictions 无效"
        _write_text(
            sample_dir / "vlm-suggestions.svg",
            _overlay_svg(
                sample,
                image_href=image_data_uri,
                boxes=suggestions,
                layer="suggestions",
                unavailable=(
                    "记录缺失"
                    if prepared is None
                    else "无可用 VLM 新增建议"
                    if prepared.review is None
                    else None
                ),
            ),
        )
        _write_text(
            sample_dir / "fused.svg",
            _overlay_svg(
                sample,
                image_href=image_data_uri,
                boxes=predictions,
                layer="fused",
                unavailable=fused_unavailable,
            ),
        )
        _write_text(
            sample_dir / "ground-truth.svg",
            _overlay_svg(
                sample,
                image_href=image_data_uri,
                boxes=sample.ground_truth,
                layer="ground-truth",
            ),
        )
        _write_text(
            sample_dir / "index.html",
            _detail_page(
                sample,
                prepared,
                image_name=image_name,
                source_available=source_available,
                raw_record_available=record is not None,
            ),
        )
        statuses = _status_messages(prepared, source_available=source_available)
        rows.append(
            "<tr>"
            f"<td>{position}</td><td>{_html(sample.sample_id)}</td>"
            f"<td>{_html('；'.join(statuses))}</td>"
            f'<td><a href="{sample_dir_name}/index.html">查看详情</a>'
            + (
                f' · <a href="{sample_dir_name}/review.json">VLM 建议（像素坐标）</a>'
                f' · <a href="{sample_dir_name}/fusion.json">采纳融合 JSON</a>'
                if record is not None
                else ""
            )
            + "</td>"
            "</tr>"
        )

    expected = len(manifest.samples)
    missing = expected - recorded
    coverage = (
        f'<div class="status" data-expected="{expected}" data-recorded="{recorded}" '
        f'data-missing="{missing}">记录覆盖：{recorded} / {expected}；缺失：{missing}</div>'
    )
    body = f"""
<h1>逐图实验依据</h1>
<p>数据集：{_html(manifest.dataset_name)}</p>
<p>划分来源：{_html(manifest.split_provenance)}</p>
{coverage}
<table><thead><tr><th>#</th><th>样本</th><th>状态</th><th>证据</th></tr></thead><tbody>{"".join(rows)}</tbody></table>
"""
    index_path = output_dir / "index.html"
    _write_text(index_path, _page("逐图实验依据", body))
    return index_path
