"""Explicit small-image tool comparison; no detector, VLM, or online task mutation."""

import argparse
import json
import time
from html import escape
from pathlib import Path

from PIL import Image
from pydantic import Field, FiniteFloat, TypeAdapter

from pipe_inspector.agent.enhancement_policy import ENHANCEMENT_POLICY_VERSION, choose_enhancement
from pipe_inspector.contracts.common import ContractModel, ImageRegion
from pipe_inspector.contracts.enhancement import EnhancementRequest
from pipe_inspector.media import MediaStore
from pipe_inspector.vision.enhancement import ENHANCEMENT_MODELS, run_enhancement
from pipe_inspector.vision.quality import analyze_quality

EVALUATION_KINDS = ("dehaze", "deblur", "super_resolution")
CHECKPOINTS = {kind: ENHANCEMENT_MODELS[kind][1] for kind in EVALUATION_KINDS}
NAMES = {"dehaze": "去雾", "deblur": "去模糊", "super_resolution": "ROI 超分 ×4"}


class EnhancementSample(ContractModel):
    sample_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    image: Path
    roi: tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat]


def _html(samples: list[dict]) -> str:
    lines = [
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        "<title>CCTV 图像增强对照</title><style>body{font:16px sans-serif;max-width:1200px;"
        "margin:2rem auto;padding:1rem}figure{margin:0}img{width:100%;max-height:640px;"
        "object-fit:contain} .pair{display:grid;grid-template-columns:1fr 1fr;gap:1rem}"
        "section{border-top:1px solid #aaa;margin-top:2rem}pre{white-space:pre-wrap}</style>",
        "<h1>CCTV 原图 / 增强图对照</h1><p>显式离线比较，每图执行三个工具；"
        "不代表自动触发，不是新增检测结果。质量分数改善不等于准确率提高，"
        "超分细节不作为缺陷或量测真值。点击图片可查看完整尺寸。</p>",
        '<p><a href="results.json">原始 JSON、模型版本、资产与坐标</a></p>',
    ]
    for sample in samples:
        sid = sample["sample_id"]
        gate = sample["automatic_gate"]
        lines.append(
            f'<section><h2>{escape(sid)}</h2><p>自动门控：不执行；'
            f'原因 {escape(gate["reason"])}；建议 {escape(gate["kind"] or "无")}。</p>'
        )
        for kind, job in sample["tools"].items():
            lines.append(f"<h3>{NAMES[kind]}</h3>")
            if job["status"] != "succeeded":
                lines.append(f'<p>失败：{escape(job["error_code"])}</p>')
                continue
            before = f"{sid}/roi.png" if kind == "super_resolution" else f"{sid}/original.png"
            after = f"{sid}/{kind}.png"
            lines.append('<div class="pair">')
            for path, title in ((before, "原图 ROI" if kind == "super_resolution" else "原图"),
                                (after, "增强图")):
                lines.append(
                    f'<figure><figcaption>{title}</figcaption><a href="{path}">'
                    f'<img src="{path}" alt="{title}"></a></figure>'
                )
            result = job["result"]
            lines.append(
                f'</div><p>{escape(result["model_id"])} / {escape(result["model_version"])}；'
                f'耗时 {job["duration_seconds"]:.3f} 秒；'
                f'输出 {result["output_asset"]["width"]}×{result["output_asset"]["height"]}。'
                "前后质量指标见 JSON；不同分辨率的锐度值不能直接当作恢复增益。</p>"
            )
        lines.append("</section>")
    return "\n".join(lines + ["</html>"])


def evaluate(
    manifest: Path, models_dir: Path, output: Path, *, device: str = "cuda:0", model_factory=None
) -> dict:
    if output.exists():
        raise FileExistsError("Use a new output directory; previous evidence is never overwritten")
    samples = TypeAdapter(list[EnhancementSample]).validate_json(manifest.read_text())
    if not 1 <= len(samples) <= 12 or len({s.sample_id for s in samples}) != len(samples):
        raise ValueError("Manifest requires 1..12 unique image samples")
    for sample in samples:
        with Image.open(sample.image) as image:
            ImageRegion(width=image.width, height=image.height, bbox_xyxy=sample.roi)
    for filename in CHECKPOINTS.values():
        if not (models_dir / filename).is_file():
            raise FileNotFoundError(f"Required local checkpoint missing: {filename}")
    if model_factory is None:
        from pipe_inspector.vision.enhancement_models import LocalEnhancementModel

        model_factory = LocalEnhancementModel
    output.mkdir(parents=True, exist_ok=False)
    media = MediaStore(output / "assets")
    records = []
    succeeded = failed = 0
    for sample in samples:
        with sample.image.open("rb") as stream:
            asset = media.ingest(stream)
        row = {
            "sample_id": sample.sample_id,
            "input_path": str(sample.image.resolve()),
            "input_asset": asset.model_dump(mode="json"),
            "automatic_gate": choose_enhancement(
                analyze_quality(media.path_for(asset.ref))
            ).model_dump(mode="json"),
            "tools": {},
        }
        directory = output / sample.sample_id
        directory.mkdir()
        with Image.open(media.path_for(asset.ref)) as source:
            source.convert("RGB").save(directory / "original.png")
        roi = ImageRegion(width=asset.width, height=asset.height, bbox_xyxy=sample.roi)
        for kind, filename in CHECKPOINTS.items():
            start = time.perf_counter()
            checkpoint = models_dir / filename
            try:
                result = run_enhancement(
                    EnhancementRequest(
                        input_asset=asset.ref,
                        kind=kind,
                        roi=roi if kind == "super_resolution" else None,
                    ),
                    media,
                    model_factory(kind, checkpoint, device),
                )
                with Image.open(media.path_for(result.output_asset.ref)) as enhanced:
                    enhanced.save(directory / f"{kind}.png")
                if kind == "super_resolution":
                    with Image.open(media.path_for(asset.ref)) as source:
                        source.crop(tuple(int(x) for x in result.source_region.bbox_xyxy)).save(
                            directory / "roi.png"
                        )
                row["tools"][kind] = {
                    "status": "succeeded",
                    "checkpoint": str(checkpoint.resolve()),
                    "result": result.model_dump(mode="json"),
                    "duration_seconds": time.perf_counter() - start,
                }
                succeeded += 1
            except (ValueError, RuntimeError, ImportError, OSError) as error:
                row["tools"][kind] = {
                    "status": "failed",
                    "checkpoint": str(checkpoint.resolve()),
                    "error_code": getattr(error, "code", type(error).__name__),
                    "duration_seconds": time.perf_counter() - start,
                }
                failed += 1
        records.append(row)
        # Save each finished sample for inspection, including visible failures.
        summary = {"succeeded": succeeded, "failed": failed, "sample_count": len(records)}
        payload = {
            "policy_version": ENHANCEMENT_POLICY_VERSION,
            "device": device,
            "summary": summary,
            "samples": records,
        }
        (output / "results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        (output / "index.html").write_text(_html(records))
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--models-dir", type=Path, default=Path("var/models/enhancement"))
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--device", default="cuda:0")
    args = parser.parse_args(argv)
    summary = evaluate(args.manifest, args.models_dir, args.output, device=args.device)
    print(json.dumps(summary))
    return int(summary["failed"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
