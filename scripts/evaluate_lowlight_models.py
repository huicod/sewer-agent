"""Compare the two approved official low-light checkpoints on real images.

This script performs no downloads and no ground-truth mean adjustment. Its
default inputs are six existing Sewer2403 evaluation originals, and all output
is written below the task's dedicated evaluation directory.
"""

from __future__ import annotations

import argparse
import html
import json
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from pipe_inspector.vision._enhancement_vendor import CIDNet, DarkIR

DEFAULT_INPUTS = (
    "var/evaluations/2026-09-11-guided/paired/gallery/sample-0025/original.jpg",
    "var/evaluations/2026-09-11-guided/paired/gallery/sample-0005/original.jpg",
    "var/evaluations/2026-09-11-guided/paired/gallery/sample-0010/original.jpg",
    "var/evaluations/2026-09-11-guided/paired/gallery/sample-0030/original.jpg",
    "var/evaluations/2026-09-11-guided/paired/gallery/sample-0009/original.jpg",
    "var/evaluations/2026-09-11-guided/paired/gallery/sample-0016/original.jpg",
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="*", type=Path, default=DEFAULT_INPUTS)
    parser.add_argument(
        "--darkir-checkpoint",
        type=Path,
        default=Path("var/models/enhancement/darkir-384-hf-8a73ebf2.pt"),
    )
    parser.add_argument(
        "--hvi-checkpoint",
        type=Path,
        default=Path("var/models/enhancement/hvi-cidnet-generalization-51481ef2.bin"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("var/evaluations/2026-09-13-adaptive-enhancement/lowlight-models"),
    )
    parser.add_argument("--device", default="cuda:0")
    return parser


def _load_models(
    darkir_checkpoint: Path,
    hvi_checkpoint: Path,
    device: torch.device,
) -> dict[str, torch.nn.Module]:
    darkir = DarkIR()
    darkir_payload = torch.load(darkir_checkpoint, weights_only=True, map_location="cpu")
    if not isinstance(darkir_payload, dict) or not isinstance(darkir_payload.get("params"), dict):
        raise ValueError("DarkIR checkpoint must contain a params state dict")
    darkir.load_state_dict(darkir_payload["params"], strict=True)

    hvi = CIDNet()
    hvi_payload = torch.load(hvi_checkpoint, weights_only=True, map_location="cpu")
    if not isinstance(hvi_payload, dict):
        raise ValueError("HVI-CIDNet checkpoint must be a state dict")
    hvi.load_state_dict(hvi_payload, strict=True)
    hvi.trans.alpha_s = 1.0
    hvi.trans.alpha = 1.0
    hvi.trans.gated = True
    hvi.trans.gated2 = True
    return {
        "darkir": darkir.to(device).eval(),
        "hvi-cidnet": hvi.to(device).eval(),
    }


def _tensor(image: Image.Image, device: torch.device) -> torch.Tensor:
    array = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
    return torch.from_numpy(array.copy()).permute(2, 0, 1).unsqueeze(0).to(device)


def _image(output: torch.Tensor) -> Image.Image:
    if output.ndim != 4 or output.shape[0] != 1 or output.shape[1] != 3:
        raise ValueError(f"model returned invalid tensor shape {tuple(output.shape)}")
    if not torch.isfinite(output).all().item():
        raise ValueError("model returned non-finite pixels")
    array = output[0].clamp(0, 1).cpu().permute(1, 2, 0).numpy()
    return Image.fromarray(np.rint(array * 255).astype(np.uint8))


def _run(
    model: torch.nn.Module,
    image: torch.Tensor,
    device: torch.device,
) -> tuple[Image.Image, float, int]:
    if device.type == "cuda":
        torch.cuda.reset_peak_memory_stats(device)
        torch.cuda.synchronize(device)
    started = time.perf_counter()
    with torch.inference_mode():
        output = model(image)
    if device.type == "cuda":
        torch.cuda.synchronize(device)
    latency_ms = (time.perf_counter() - started) * 1000.0
    peak_bytes = torch.cuda.max_memory_allocated(device) if device.type == "cuda" else 0
    return _image(output), latency_ms, peak_bytes


def _write_index(output: Path, records: list[dict[str, object]]) -> None:
    cards = []
    for record in records:
        sample = html.escape(str(record["sample"]))
        metrics = record["models"]
        cards.append(
            f"<section><h2>{sample}</h2><div class='row'>"
            f"<figure><img src='{sample}/original.png'><figcaption>Original</figcaption></figure>"
            f"<figure><img src='{sample}/darkir.png'><figcaption>DarkIR — "
            f"{metrics['darkir']['latency_ms']:.1f} ms</figcaption></figure>"
            f"<figure><img src='{sample}/hvi-cidnet.png'><figcaption>HVI-CIDNet — "
            f"{metrics['hvi-cidnet']['latency_ms']:.1f} ms</figcaption></figure>"
            "</div></section>"
        )
    document = """<!doctype html><meta charset='utf-8'><title>Low-light models</title>
<style>body{font-family:sans-serif;margin:2rem;background:#181818;color:#eee}.row{display:grid;
grid-template-columns:repeat(3,minmax(0,1fr));gap:1rem}figure{margin:0}img{width:100%;height:auto}
figcaption{text-align:center;padding:.5rem}section{margin-bottom:2rem}</style>""" + "".join(cards)
    (output / "index.html").write_text(document, encoding="utf-8")


def main() -> None:
    args = _parser().parse_args()
    device = torch.device(args.device)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but is unavailable")
    output = args.output
    if output.exists():
        raise FileExistsError("use a new output directory; evidence is not overwritten")
    output.mkdir(parents=True, exist_ok=False)
    models = _load_models(args.darkir_checkpoint, args.hvi_checkpoint, device)
    records: list[dict[str, object]] = []
    try:
        for index, input_path in enumerate(args.inputs, start=1):
            input_path = Path(input_path)
            with Image.open(input_path) as loaded:
                original = loaded.convert("RGB")
            sample = f"sample-{index:02d}-{input_path.parent.name}"
            sample_dir = output / sample
            sample_dir.mkdir()
            original.save(sample_dir / "original.png")
            input_tensor = _tensor(original, device)
            model_records: dict[str, dict[str, float | int]] = {}
            for model_name, model in models.items():
                enhanced, latency_ms, peak_bytes = _run(model, input_tensor, device)
                if enhanced.size != original.size:
                    raise ValueError(f"{model_name} changed spatial size")
                enhanced.save(sample_dir / f"{model_name}.png")
                model_records[model_name] = {
                    "latency_ms": round(latency_ms, 3),
                    "peak_memory_bytes": peak_bytes,
                }
            record: dict[str, object] = {
                "sample": sample,
                "source": str(input_path),
                "width": original.width,
                "height": original.height,
                "models": model_records,
            }
            (sample_dir / "metadata.json").write_text(
                json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            records.append(record)
    finally:
        for model in models.values():
            model.cpu()
        del models
        if device.type == "cuda":
            torch.cuda.empty_cache()
    manifest = {
        "device": str(device),
        "torch": torch.__version__,
        "darkir": {
            "model_id": "darkir-384",
            "checkpoint": str(args.darkir_checkpoint),
            "source_revision": "Cidaut/DarkIR@8a73ebf271c513dfda9d1df444762d8aa0b42138",
            "code_revision": "cidautai/DarkIR@0910291157ad04f210481a002cf6bb480f6a7f18",
        },
        "hvi_cidnet": {
            "model_id": "hvi-cidnet-generalization",
            "checkpoint": str(args.hvi_checkpoint),
            "source_revision": (
                "Fediory/HVI-CIDNet-Generalization@"
                "51481ef2546f870060c43eb6d6525399f5b3d2d3"
            ),
            "code_revision": "Fediory/HVI-CIDNet@eb43d7d91e9a336c66856824ff9e4603ae41f408",
        },
        "settings": {"gt_mean_adjustment": False, "gamma": 1.0, "alpha": 1.0},
        "records": records,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    _write_index(output, records)


if __name__ == "__main__":
    main()
