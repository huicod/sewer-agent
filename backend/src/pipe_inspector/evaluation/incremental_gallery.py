"""Static per-sample evidence gallery for fixed incremental evaluations."""

from __future__ import annotations

import json
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from pipe_inspector.contracts.open_scan import OpenCandidate, OpenDetectionBatch

from .gallery import _data_uri, _html, _number, _page
from .review import LabeledBox

_FIVE_CLASS_LABELS = {"AJ", "BX", "FS", "PL", "BLOCK"}
_COLOURS = {
    "primary": "#22d3ee",
    "vlm": "#c084fc",
    "guided": "#fb923c",
    "open": "#f472b6",
    "combined": "#facc15",
    "annotations": "#4ade80",
}
_PIL_FORMATS = {
    "BMP": (".bmp", "image/bmp"),
    "GIF": (".gif", "image/gif"),
    "JPEG": (".jpg", "image/jpeg"),
    "PNG": (".png", "image/png"),
    "TIFF": (".tiff", "image/tiff"),
    "WEBP": (".webp", "image/webp"),
}


@dataclass(frozen=True)
class _DrawBox:
    label: str
    bbox_xyxy: tuple[float, float, float, float]
    score: float | None = None
    name: str | None = None
    status: str | None = None


def _labeled_boxes(values: object) -> tuple[_DrawBox, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValueError("box stage must be a list")
    result = []
    for value in values:
        box = LabeledBox.model_validate(value)
        result.append(
            _DrawBox(
                label=box.label.value,
                bbox_xyxy=tuple(float(item) for item in box.bbox_xyxy),
                score=float(box.score),
            )
        )
    return tuple(result)


def _primary_from_observation(observation: object) -> tuple[_DrawBox, ...] | None:
    if not isinstance(observation, Mapping):
        return None
    detection = observation.get("detection")
    if not isinstance(detection, Mapping) or "detections" not in detection:
        return None
    values = detection["detections"]
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        return None
    boxes = []
    for value in values:
        if not isinstance(value, Mapping):
            return None
        try:
            box = LabeledBox.model_validate(
                {
                    "label": value["label"],
                    "bbox_xyxy": value["bbox_xyxy"],
                    "score": value["score"],
                }
            )
        except (KeyError, TypeError, ValueError):
            return None
        boxes.append(
            _DrawBox(
                label=box.label.value,
                bbox_xyxy=tuple(float(item) for item in box.bbox_xyxy),
                score=float(box.score),
            )
        )
    return tuple(boxes)


def _suggestions(review: object) -> tuple[_DrawBox, ...] | None:
    if not isinstance(review, Mapping):
        return None
    raw_items = review.get("additional_anomalies")
    if isinstance(raw_items, (str, bytes)) or not isinstance(raw_items, Sequence):
        return None
    boxes = []
    for item in raw_items:
        if not isinstance(item, Mapping) or item.get("bbox_xyxy") is None:
            continue
        bbox = item["bbox_xyxy"]
        if isinstance(bbox, (str, bytes)) or not isinstance(bbox, Sequence) or len(bbox) != 4:
            continue
        try:
            coordinates = tuple(float(value) for value in bbox)
        except (TypeError, ValueError):
            continue
        boxes.append(
            _DrawBox(
                label=str(item.get("label", "UNKNOWN")),
                name=None if item.get("name") is None else str(item["name"]),
                bbox_xyxy=coordinates,
            )
        )
    return tuple(boxes)


def _candidate_key(candidate: OpenCandidate) -> tuple[object, ...]:
    return (
        candidate.label.value if hasattr(candidate.label, "value") else str(candidate.label),
        candidate.name,
        float(candidate.score),
        tuple(float(value) for value in candidate.bbox_xyxy),
    )


def _open_candidates(record: Mapping[str, Any]) -> tuple[tuple[_DrawBox, ...], list[dict]]:
    batch = OpenDetectionBatch.model_validate(record["open_batch"])
    raw_added = record.get("open_added", ())
    if isinstance(raw_added, (str, bytes)) or not isinstance(raw_added, Sequence):
        raise ValueError("open_added must be a list")
    added = {_candidate_key(OpenCandidate.model_validate(item)) for item in raw_added}
    boxes = []
    evidence = []
    for candidate in batch.candidates:
        key = _candidate_key(candidate)
        actually_added = key in added
        label = candidate.label.value if hasattr(candidate.label, "value") else str(candidate.label)
        bbox = tuple(float(value) for value in candidate.bbox_xyxy)
        boxes.append(
            _DrawBox(
                label=label,
                name=candidate.name,
                bbox_xyxy=bbox,
                score=float(candidate.score),
                status="actually-added" if actually_added else "not-added",
            )
        )
        evidence.append(
            {
                "label": label,
                "name": candidate.name,
                "score": float(candidate.score),
                "bbox_xyxy": list(bbox),
                "actually_added": actually_added,
            }
        )
    return tuple(boxes), evidence


def _placeholder_svg(width: int, height: int, message: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">\n'
        '  <rect width="100%" height="100%" fill="#111827"/>\n'
        f'  <text x="50%" y="50%" text-anchor="middle" dominant-baseline="middle" '
        f'fill="#fca5a5" font-size="14">{_html(message)}</text>\n'
        "</svg>\n"
    )


def _copy_source(sample: Mapping[str, Any], directory: Path) -> tuple[str, str, bool]:
    width, height = int(sample["width"]), int(sample["height"])
    path = Path(str(sample["image_path"]))
    try:
        with Image.open(path) as source:
            source.load()
            if source.size != (width, height):
                raise ValueError("source dimensions changed")
            suffix, mime_type = _PIL_FORMATS[str(source.format).upper()]
    except (OSError, ValueError, KeyError):
        name = "original-unavailable.svg"
        data = _placeholder_svg(width, height, "original file unavailable").encode()
        (directory / name).write_bytes(data)
        return name, _data_uri(data, "image/svg+xml"), False
    name = f"original{suffix}"
    destination = directory / name
    shutil.copyfile(path, destination)
    return name, _data_uri(destination.read_bytes(), str(mime_type)), True


def _overlay_svg(
    sample: Mapping[str, Any],
    *,
    title: str,
    image_href: str,
    boxes: Sequence[_DrawBox],
    colour_key: str,
    empty_message: str,
) -> str:
    width, height = int(sample["width"]), int(sample["height"])
    colour = _COLOURS[colour_key]
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}">'
        ),
        f"  <title>{_html(sample['sample_id'])} · {_html(title)}</title>",
        (
            f'  <image href="{_html(image_href)}" x="0" y="0" width="{width}" '
            f'height="{height}" preserveAspectRatio="none"/>'
        ),
    ]
    for index, box in enumerate(boxes):
        x1, y1, x2, y2 = box.bbox_xyxy
        name_attribute = "" if box.name is None else f' data-name="{_html(box.name)}"'
        score_attribute = "" if box.score is None else f' data-score="{_number(box.score)}"'
        status_attribute = "" if box.status is None else f' data-status="{_html(box.status)}"'
        label_y = min(max(14.0, y1 - 3), max(0.0, height - 2))
        visible_name = "" if box.name is None else f" · {box.name}"
        visible_status = "" if box.status is None else f" · {box.status}"
        visible_score = "" if box.score is None else f" · {box.score:.3f}"
        lines.extend(
            [
                (
                    f'  <rect data-box-index="{index}" data-label="{_html(box.label)}"'
                    f'{name_attribute}{score_attribute}{status_attribute} x="{_number(x1)}" '
                    f'y="{_number(y1)}" width="{_number(x2 - x1)}" '
                    f'height="{_number(y2 - y1)}" fill="none" stroke="{colour}" '
                    'stroke-width="2" vector-effect="non-scaling-stroke"/>'
                ),
                (
                    f'  <text data-box-index="{index}" x="{_number(x1)}" '
                    f'y="{_number(label_y)}" fill="{colour}" font-size="12" '
                    'font-weight="700" stroke="#111827" stroke-width="3" paint-order="stroke">'
                    f"{_html(f'#{index} {box.label}{visible_name}{visible_score}{visible_status}')}"
                    "</text>"
                ),
            ]
        )
    if not boxes:
        lines.extend(
            [
                f'  <rect x="0" y="0" width="{width}" height="24" fill="#374151" opacity="0.92"/>',
                f'  <text x="8" y="17" fill="#fff" font-size="13">{_html(empty_message)}</text>',
            ]
        )
    lines.append("</svg>")
    return "\n".join(lines) + "\n"


def _write_text(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8")


def _stage_panel(title: str, filename: str, note: str) -> str:
    return (
        f'<section class="panel"><h2>{_html(title)}</h2><p class="muted">{_html(note)}</p>'
        f'<a href="{filename}" download>download layer</a>'
        f'<img src="{filename}" alt="{_html(title)}"></section>'
    )


def _open_status_markup(record: Mapping[str, Any] | None, candidate_count: int) -> str:
    if record is None:
        return '<div class="status" data-open-status="missing">incremental record missing</div>'
    status = str(record.get("open_status", "failed"))
    error = record.get("open_error")
    trigger = record.get("trigger")
    duration = record.get("open_duration_seconds")
    if status == "succeeded":
        added_count = len(record.get("open_added", ()))
        return (
            '<div class="status" data-open-status="succeeded">Open scan: SUCCEEDED; '
            f"all candidates: {candidate_count}; actually added: {added_count}; "
            f"trigger: {_html(trigger)}; duration: {_html(duration)} seconds.</div>"
        )
    if status == "skipped":
        return (
            '<div class="status" data-open-status="skipped">Open scan: SKIPPED '
            f"(not an empty success); trigger: {_html(trigger)}.</div>"
        )
    return (
        '<div class="status" data-open-status="failed">Open scan: FAILED '
        f"(not an empty success); error: {_html(error)}; trigger: {_html(trigger)}.</div>"
    )


def _truth_markup(sample: Mapping[str, Any]) -> str:
    source_kind = str(sample["source_kind"])
    ground_truth = sample.get("ground_truth")
    if source_kind == "video" and ground_truth is None:
        return (
            '<div class="status" data-ground-truth="unknown">Ground truth: UNKNOWN '
            "(video frame is unannotated; accuracy is not computed).</div>"
        )
    if ground_truth is None:
        return (
            '<div class="status" data-ground-truth="unknown">Ground truth: UNKNOWN '
            "(no original annotations supplied; accuracy is not computed).</div>"
        )
    return (
        f'<div class="status" data-ground-truth="available">Original annotations: '
        f"{len(ground_truth)}. Unmatched annotations are scoring evidence, not verified false "
        "positives.</div>"
    )


def _policy_markup(sample: Mapping[str, Any]) -> str:
    policy = str(sample["baseline_policy"])
    if str(sample["source_kind"]) == "video":
        return (
            f'<div class="status" data-baseline-policy="{_html(policy)}">Video baseline policy: '
            f"{_html(policy)}; differs from image guided-confidence-v2 and must not be compared "
            "as the same review policy.</div>"
        )
    return (
        f'<div class="status" data-baseline-policy="{_html(policy)}">Image baseline policy: '
        f"{_html(policy)}.</div>"
    )


def _locator_markup(sample: Mapping[str, Any]) -> str:
    if str(sample["source_kind"]) != "video":
        return ""
    return (
        f"<p>Video locator: frame {_html(sample.get('frame_index'))} at "
        f"{_html(sample.get('timestamp_seconds'))} seconds.</p>"
    )


def _source_pins_markup(sample: Mapping[str, Any]) -> str:
    pins = json.dumps(
        sample.get("source_pins", {}), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    return (
        "<details><summary>Source pins: 来源与版本（展开）</summary>"
        f"<pre>{_html(pins)}</pre></details>"
    )


def _review_status_markup(record: Mapping[str, Any] | None) -> str:
    if record is None:
        return ""
    error = record.get("review_error")
    if error is not None:
        return (
            '<div class="status" data-review-status="failed">VLM review unavailable: '
            f"{_html(error)}</div>"
        )
    if record.get("review") is None:
        return (
            '<div class="status" data-review-status="unavailable">'
            "VLM review unavailable; no suggestion layer was produced.</div>"
        )
    return '<div class="status" data-review-status="available">VLM review available.</div>'


def _detail_page(
    sample: Mapping[str, Any],
    record: Mapping[str, Any] | None,
    *,
    source_name: str,
    source_available: bool,
    panels: Sequence[str],
    candidate_count: int,
) -> str:
    source_status = (
        ""
        if source_available
        else '<div class="status" data-source="unavailable">original file unavailable</div>'
    )
    combined_note = (
        '<div class="status">combined is a pending review queue, not confirmed accuracy. '
        "OTHER remains pending human review and is excluded from five-class combined.</div>"
        if record is not None
        else ""
    )
    body = f"""
<p><a href="../index.html">← gallery index</a> · <a href="evidence.json">raw evidence JSON</a></p>
<h1>{_html(sample["sample_id"])}</h1>
<p>Group: {_html(sample["group_id"])} · source: {_html(sample["source_kind"])} ·
dimensions: {int(sample["width"])} × {int(sample["height"])}</p>
{_locator_markup(sample)}
{_source_pins_markup(sample)}
{source_status}
{_policy_markup(sample)}
{_truth_markup(sample)}
{_review_status_markup(record)}
{_open_status_markup(record, candidate_count)}
{combined_note}
<div class="grid">
{
        _stage_panel(
            "Original source",
            source_name,
            "Copied source pixels; unavailable sources use an explicit placeholder.",
        )
    }
{"".join(panels)}
</div>
"""
    return _page(f"Incremental evidence · {sample['sample_id']}", body)


def _validate_inputs(
    samples: list[dict], records: list[dict]
) -> tuple[list[Mapping[str, Any]], dict[str, Mapping[str, Any]]]:
    sample_ids: set[str] = set()
    prepared_samples = []
    for sample in samples:
        sample_id = str(sample.get("sample_id", ""))
        if not sample_id:
            raise ValueError("gallery sample_id must be nonempty")
        if sample_id in sample_ids:
            raise ValueError(f"duplicate gallery sample_id: {sample_id}")
        if int(sample["width"]) <= 0 or int(sample["height"]) <= 0:
            raise ValueError("gallery dimensions must be positive")
        sample_ids.add(sample_id)
        prepared_samples.append(sample)
    records_by_id = {}
    for record in records:
        sample_id = str(record.get("sample_id", ""))
        if sample_id in records_by_id:
            raise ValueError(f"duplicate gallery record for sample_id: {sample_id}")
        if sample_id not in sample_ids:
            raise ValueError(f"gallery record is not present in samples: {sample_id}")
        records_by_id[sample_id] = record
    return prepared_samples, records_by_id


def export_incremental_gallery(samples: list[dict], records: list[dict], output_dir: Path) -> Path:
    """Export stable, self-contained visual evidence for each incremental sample."""
    prepared_samples, records_by_id = _validate_inputs(samples, records)
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = []

    for position, sample in enumerate(prepared_samples, start=1):
        directory_name = f"sample-{position:04d}"
        directory = output_dir / directory_name
        directory.mkdir(parents=True, exist_ok=True)
        source_name, image_href, source_available = _copy_source(sample, directory)
        record = records_by_id.get(str(sample["sample_id"]))
        panels = []
        links: dict[str, str] = {"detail": "index.html", "source": source_name}

        primary = (
            _labeled_boxes(record["primary"])
            if record is not None and "primary" in record
            else _primary_from_observation(sample.get("observation"))
        )
        if primary is not None:
            filename = "primary.svg"
            _write_text(
                directory / filename,
                _overlay_svg(
                    sample,
                    title="Primary / YOLO overlay",
                    image_href=image_href,
                    boxes=primary,
                    colour_key="primary",
                    empty_message="0 primary boxes (completed stage)",
                ),
            )
            panels.append(
                _stage_panel("Primary / YOLO overlay", filename, "Primary detector output.")
            )
            links["primary"] = filename

        review = record.get("review") if record is not None else sample.get("review")
        suggestions = _suggestions(review)
        if suggestions is not None:
            filename = "vlm-suggestions.svg"
            _write_text(
                directory / filename,
                _overlay_svg(
                    sample,
                    title="VLM suggestions",
                    image_href=image_href,
                    boxes=suggestions,
                    colour_key="vlm",
                    empty_message="0 localized VLM suggestions (review available)",
                ),
            )
            panels.append(
                _stage_panel(
                    "VLM suggestions",
                    filename,
                    "Advisory suggestions before baseline fusion; not final predictions.",
                )
            )
            links["vlm_suggestions"] = filename

        guided_values = (
            record["guided"]
            if record is not None and "guided" in record
            else sample.get("baseline_predictions")
        )
        if guided_values is not None:
            guided = _labeled_boxes(guided_values)
            filename = "baseline-guided.svg"
            _write_text(
                directory / filename,
                _overlay_svg(
                    sample,
                    title="Baseline-guided overlay",
                    image_href=image_href,
                    boxes=guided,
                    colour_key="guided",
                    empty_message="0 baseline-guided boxes (completed stage)",
                ),
            )
            panels.append(
                _stage_panel(
                    "Baseline-guided overlay",
                    filename,
                    f"Saved baseline under {sample['baseline_policy']}.",
                )
            )
            links["baseline_guided"] = filename

        open_boxes: tuple[_DrawBox, ...] = ()
        open_evidence: list[dict] = []
        if (
            record is not None
            and record.get("open_status") == "succeeded"
            and record.get("open_batch") is not None
        ):
            open_boxes, open_evidence = _open_candidates(record)
            filename = "open-candidates.svg"
            _write_text(
                directory / filename,
                _overlay_svg(
                    sample,
                    title="Open candidates",
                    image_href=image_href,
                    boxes=open_boxes,
                    colour_key="open",
                    empty_message="0 open candidates (successful scan)",
                ),
            )
            panels.append(
                _stage_panel(
                    "Open candidates overlay",
                    filename,
                    "Shows all scan candidates; each box says whether it was actually added.",
                )
            )
            links["open_candidates"] = filename

        if record is not None and record.get("combined") is not None:
            combined = _labeled_boxes(record["combined"])
            if any(box.label not in _FIVE_CLASS_LABELS for box in combined):
                raise ValueError("combined stage must contain only five-class boxes")
            filename = "combined-tentative.svg"
            _write_text(
                directory / filename,
                _overlay_svg(
                    sample,
                    title="Combined tentative overlay",
                    image_href=image_href,
                    boxes=combined,
                    colour_key="combined",
                    empty_message="0 combined boxes (pending queue, not confirmed normal)",
                ),
            )
            panels.append(
                _stage_panel(
                    "Combined tentative overlay",
                    filename,
                    "Guided five-class boxes plus added open five-class candidates; "
                    "pending review.",
                )
            )
            links["combined_tentative"] = filename

        ground_truth = sample.get("ground_truth")
        if str(sample["source_kind"]) == "image" and ground_truth is not None:
            annotations = _labeled_boxes(ground_truth)
            filename = "original-annotations.svg"
            _write_text(
                directory / filename,
                _overlay_svg(
                    sample,
                    title="Original annotations",
                    image_href=image_href,
                    boxes=annotations,
                    colour_key="annotations",
                    empty_message="0 original annotation boxes (not a claim of normality)",
                ),
            )
            panels.append(
                _stage_panel(
                    "Original annotation overlay",
                    filename,
                    "Original dataset annotation; unmatched predictions are not verified "
                    "false positives.",
                )
            )
            links["original_annotations"] = filename

        evidence = {
            "sample": sample,
            "record": record,
            "open_status": "record_missing" if record is None else record.get("open_status"),
            "open_error": None if record is None else record.get("open_error"),
            "trigger": None if record is None else record.get("trigger"),
            "open_duration_seconds": (
                None if record is None else record.get("open_duration_seconds")
            ),
            "open_candidates": open_evidence,
            "links": links,
        }
        _write_text(
            directory / "evidence.json",
            json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        links["raw_evidence"] = "evidence.json"
        _write_text(
            directory / "index.html",
            _detail_page(
                sample,
                record,
                source_name=source_name,
                source_available=source_available,
                panels=panels,
                candidate_count=len(open_boxes),
            ),
        )

        open_status = "record missing" if record is None else str(record.get("open_status"))
        source_status = "available" if source_available else "original file unavailable"
        action_links = [
            f'<a href="{directory_name}/index.html">details</a>',
            f'<a href="{directory_name}/evidence.json">raw JSON</a>',
        ]
        for key in ("primary", "baseline_guided", "open_candidates", "combined_tentative"):
            if key in links:
                action_links.append(
                    f'<a href="{directory_name}/{links[key]}">{_html(key.replace("_", " "))}</a>'
                )
        rows.append(
            "<tr>"
            f"<td>{position}</td><td>{_html(sample['sample_id'])}</td>"
            f"<td>{_html(sample['source_kind'])}</td><td>{_html(sample['baseline_policy'])}</td>"
            f"<td>{_html(open_status)}</td><td>{_html(source_status)}</td>"
            f"<td>{' · '.join(action_links)}</td></tr>"
        )

    expected = len(prepared_samples)
    recorded = len(records_by_id)
    missing = expected - recorded
    coverage = (
        f'<div class="status" data-expected="{expected}" data-recorded="{recorded}" '
        f'data-missing="{missing}">Incremental record coverage: {recorded} / {expected}; '
        f"missing: {missing}.</div>"
    )
    body = f"""
<h1>Fixed incremental evaluation evidence</h1>
<p>固定集逐图对比：原检测 → 已有复核融合 → 条件开放检测 → 待审联合结果。
联合层不是自动确认结论；视频没有人工真值，不计算准确率。</p>
<p>Each combined layer is a pending review queue, not an automatically confirmed result.</p>
{coverage}
<table><thead><tr><th>#</th><th>sample</th><th>source</th><th>baseline policy</th>
<th>open status</th><th>source file</th><th>evidence</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table>
"""
    index_path = output_dir / "index.html"
    _write_text(index_path, _page("Fixed incremental evaluation evidence", body))
    return index_path


__all__ = ["export_incremental_gallery"]
