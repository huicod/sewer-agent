"""Offline alternative-detector comparison against a frozen T11 image/VLM baseline."""

import argparse
import html
import json
from pathlib import Path

from pipe_inspector.contracts.open_scan import OpenDetectionBatch
from pipe_inspector.evaluation.guided_run import _write_json
from pipe_inspector.evaluation.incremental import (
    _record,
    load_guided_samples,
    run_incremental,
    validate_fixed_cohort,
    validate_samples,
)
from pipe_inspector.evaluation.incremental_gallery import export_incremental_gallery
from pipe_inspector.evaluation.incremental_metrics import export_review_queue, summarize_incremental


def load_reference(reference_path: Path, manifest_path: Path, guided_paths: list[Path]) -> dict:
    """Pin the original 48 identities AND accepted cached baseline, not just a count."""
    reference = json.loads(reference_path.read_text())
    validate_fixed_cohort(reference["samples"], json.loads(manifest_path.read_text()))
    samples = validate_samples([s for p in guided_paths for s in load_guided_samples(p)])
    expected = [s for s in reference["samples"] if s["source_kind"] == "image"]
    if samples != expected:
        raise ValueError("reference image baseline differs from accepted caches")
    records = [r for r in reference["records"] if r["source_kind"] == "image"]
    if [r["sample_id"] for r in records] != [s["sample_id"] for s in samples]:
        raise ValueError("reference image coverage differs")
    pins = reference["pins"]["detector"]
    if pins["detector_id"] != "yoloe":
        raise ValueError("reference must be the existing YOLOE run")
    for sample, record in zip(samples, records, strict=True):
        batch = (
            OpenDetectionBatch.model_validate(record["open_batch"])
            if record["open_batch"]
            else None
        )
        if batch and any(getattr(batch, key) != value for key, value in pins.items()):
            raise ValueError("reference detector pins differ")
        if record != _record(
            sample,
            batch,
            record["open_status"],
            record["open_error"],
            record["open_duration_seconds"],
        ):
            raise ValueError("reference records differ from frozen baseline/fusion")
    return reference | {"samples": samples, "records": records}


def export_run(state: dict, output: Path) -> dict:
    summary = summarize_incremental(state["records"], expected_count=len(state["samples"]))
    _write_json(output / "summary.json", summary)
    export_review_queue(state["records"], output / "review-queue.csv", samples=state["samples"])
    export_incremental_gallery(state["samples"], state["records"], output / "gallery")
    return summary


def run_model(samples, output: Path, *, detector, configuration: dict) -> dict:
    try:
        state = run_incremental(
            samples,
            output,
            detector=detector,
            configuration=configuration,
            unload_after_sample=False,
        )
    finally:
        detector.unload()
    export_run(state, output)
    return state


def export_comparison(states: dict[str, dict], output: Path) -> dict:
    """Show raw candidate boxes alongside the immutable VLM baseline, including failures."""
    baseline = next(iter(states.values()))
    ids = [s["sample_id"] for s in baseline["samples"]]
    baseline_keys = ("primary", "guided", "observation", "review", "review_error", "trigger")
    result = {
        "sample_count": len(ids),
        "new_vlm_calls": 0,
        "note": "候选均待人工确认；原标注可能遗漏，不把新增框数量当准确率提升。",
        "models": {},
    }
    for name, state in states.items():
        if [r["sample_id"] for r in state["records"]] != ids:
            raise ValueError("comparison record coverage differs")
        if state["samples"] != baseline["samples"] or any(
            any(left[k] != right[k] for k in baseline_keys)
            for left, right in zip(baseline["records"], state["records"], strict=True)
        ):
            raise ValueError("comparison baseline differs")
        summary = summarize_incremental(state["records"], expected_count=len(ids))
        operations = summary["operations"]["open_detection"]
        cohort = summary["cohorts"]["image"]
        result["models"][name] = {
            "pins": state["pins"],
            "status_counts": operations["status_counts"],
            "raw_candidates": sum(
                len(r["open_batch"]["candidates"]) for r in state["records"] if r["open_batch"]
            ),
            "pending_added": operations["pending_count"],
            "pending_other": operations["pending_other_count"],
            "added_annotation_matches": sum(
                d["guided_to_combined"]["recovered"] for d in cohort["per_image_deltas"]
            ),
            "lost_annotation_matches": sum(
                d["guided_to_combined"]["lost"] for d in cohort["per_image_deltas"]
            ),
            "annotation_metrics": cohort["stages"],
            "duration_seconds": operations["duration_seconds"],
        }
    output.mkdir(parents=True, exist_ok=True)
    _write_json(output / "comparison.json", result)
    panels = []
    first_name = html.escape(next(iter(states)), quote=True)
    for index, sample_id in enumerate(ids, 1):
        folder = f"gallery/sample-{index:04d}"
        cells = [
            "<figure><figcaption>已接受的 VLM 融合基线（保持不变）</figcaption>"
            f'<img loading="lazy" src="{first_name}/{folder}/baseline-guided.svg"></figure>'
        ]
        for name, state in states.items():
            escaped = html.escape(name, quote=True)
            record = state["records"][index - 1]
            status = record["open_status"]
            label = html.escape(
                ", ".join(f"{c['label']} {c['score']:.3f}" for c in record["open_added"])
                or "无新增"
            )
            image = (
                f'<img loading="lazy" src="{escaped}/{folder}/open-candidates.svg">'
                if status == "succeeded"
                else f"<p>{html.escape(status)}</p>"
            )
            cells.append(
                f"<figure><figcaption>{escaped} · {status} · 新增待审：{label}</figcaption>"
                f'{image}<a href="{escaped}/{folder}/index.html">'
                '原图、YOLO、VLM、补检与合并全流程</a>'
                "</figure>"
            )
        panels.append(
            f'<section id="sample-{index:04d}"><h2>sample-{index:04d}</h2>'
            f'<p>{html.escape(sample_id)}</p><div class="row">{"".join(cells)}</div></section>'
        )
    document = (
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
        "<title>开放词表补检对比</title><style>"
        "body{font:15px system-ui;margin:24px;background:#f5f6fa;color:#17212b}"
        ".row{display:flex;gap:12px;align-items:start}figure{flex:1;min-width:0;margin:0}"
        "img{width:100%}figcaption{min-height:65px}"
        "section{background:white;padding:16px;margin:20px 0}"
        "@media(max-width:900px){.row{display:grid;grid-template-columns:1fr 1fr}}"
        "</style><h1>同图、同 VLM 基线的补检对比</h1>"
        f"<p>{html.escape(result['note'])}</p>"
        "<p>补检列显示模型原始候选；“新增待审”是去除与已有结果重复框后的数量。"
        "skipped 表示原流程未触发，failed 表示失败，均不是正常结论。"
        "模型阈值、分辨率及加载策略不同，耗时不能直接视作等条件速度排名。</p>"
        '<p><a href="comparison.json">完整统计与模型参数</a></p>' + "".join(panels) + "</html>"
    )
    (output / "index.html").write_text(document, encoding="utf-8")
    return result


def cli_main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--fixed-manifest", type=Path, required=True)
    parser.add_argument("--guided-state", type=Path, action="append", required=True)
    parser.add_argument("--models-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", choices=("mm-gdino-base", "owlv2-large"), action="append")
    parser.add_argument("--device", default="cuda:0")
    args = parser.parse_args()
    from pipe_inspector.vision.hf_open_detector import HFOpenDetector

    reference = load_reference(args.reference, args.fixed_manifest, args.guided_state)
    states = {"yoloe": reference}
    export_run(reference, args.output / "yoloe")
    for name in args.model or ("mm-gdino-base", "owlv2-large"):
        checkpoint = (args.models_root / name).resolve()
        detector = HFOpenDetector(checkpoint, model_key=name, device=args.device)
        print(
            f"Running {name}: {len(reference['samples'])} fixed images (conditional scan)",
            flush=True,
        )
        states[name] = run_model(
            reference["samples"],
            args.output / name,
            detector=detector,
            configuration={
                "model_path": str(checkpoint),
                "device": args.device,
                "confidence": detector.confidence,
                "preprocessing": "native model processor; original RGB",
                "transformers_version": "4.57.6",
                "processor_use_fast": name == "mm-gdino-base",
                "precision": detector.precision,
                "scores": (
                    "mean sigmoid over caption concept token spans; argmax per query"
                    if name == "mm-gdino-base" else "native OWLv2 query confidence"
                ),
                "model_lifetime": "one offline session; first call includes loading",
                "postprocessing": "same-concept NMS 0.5; max100; original coordinates",
            },
        )
        comparison = export_comparison(states, args.output)
        print(json.dumps(comparison["models"][name], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    cli_main()
