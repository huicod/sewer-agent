"""Fixed, two-image local GPU compatibility evidence; not an accuracy experiment."""

import json
import time
from pathlib import Path
from tempfile import mkdtemp

import torch
from PIL import Image, ImageDraw
from pipe_inspector.config import Settings
from pipe_inspector.vision.detector import UltralyticsDetector
from pipe_inspector.vision.open_detector import YOLOEOpenDetector


def draw(source, batch, output, *, open_scan=False):
    with Image.open(source) as original:
        image = original.convert("RGB")
    pen = ImageDraw.Draw(image)
    for box in batch.candidates if open_scan else batch.detections:
        pen.rectangle(box.bbox_xyxy, outline="red" if open_scan else "blue", width=3)
        name = box.name if open_scan else box.label.value
        pen.text(box.bbox_xyxy[:2], f"{name} {box.score:.2f}", fill="red" if open_scan else "blue")
    image.save(output)


def main():
    Path("var/open-scans").mkdir(parents=True, exist_ok=True)
    output = Path(mkdtemp(prefix="2026-09-11-t10-", dir="var/open-scans"))
    print(f"output={output}", flush=True)
    primary = UltralyticsDetector(Settings().detector_path, device="0")
    opened = YOLOEOpenDetector(
        Path("var/models/yoloe/yoloe-26s-seg.pt"),
        Path("var/models/yoloe/mobileclip2_b.ts"),
        device="0",
        confidence=0.25,
    )
    records = []
    try:
        for sample in ("sample-0007", "sample-0018"):
            path = (
                Path("var/evaluations/2026-09-11-confidence/paired/gallery")
                / sample
                / "original.jpg"
            )
            before = torch.cuda.memory_allocated()
            started = time.perf_counter()
            detection = primary.predict_image(path)
            primary_seconds = time.perf_counter() - started
            primary.unload()
            after_primary = torch.cuda.memory_allocated()
            started = time.perf_counter()
            candidate = opened.predict_image(path)
            open_seconds = time.perf_counter() - started
            # A second call exercises cached embeddings with the same instance.
            started = time.perf_counter()
            repeated = opened.predict_image(path)
            warm_seconds = time.perf_counter() - started
            assert repeated == candidate
            assert (detection.width, detection.height) == (candidate.width, candidate.height)
            allocated = torch.cuda.memory_allocated()
            opened.unload()
            after_open = torch.cuda.memory_allocated()
            print(
                dict(
                    before=before,
                    after_primary=after_primary,
                    allocated=allocated,
                    after_open=after_open,
                ),
                flush=True,
            )
            # First CUDA inference creates a persistent cuBLAS workspace (32 MiB here).
            # Diagnostic clearing that workspace returned allocation to zero; it is not
            # a retained model. Compare open unload to the initialized runtime baseline.
            assert after_open <= after_primary + 1024 * 1024
            if records:
                assert after_primary <= before + 1024 * 1024
            draw(path, detection, output / f"{sample}-primary.png")
            draw(path, candidate, output / f"{sample}-open.png", open_scan=True)
            record = dict(
                sample=sample,
                source=str(path),
                primary=detection.model_dump(mode="json"),
                open_scan=candidate.model_dump(mode="json"),
                primary_seconds=primary_seconds,
                open_seconds=open_seconds,
                open_warm_seconds=warm_seconds,
                memory=dict(
                    before=before,
                    after_primary_unload=after_primary,
                    open_allocated=allocated,
                    after_open_unload=after_open,
                ),
            )
            records.append(record)
            print(
                json.dumps(
                    {k: v for k, v in record.items() if k not in {"primary", "open_scan"}}
                    | dict(
                        primary_count=len(detection.detections),
                        open_count=len(candidate.candidates),
                    )
                ),
                flush=True,
            )
    finally:
        primary.unload()
        opened.unload()
    (output / "results.json").write_text(
        json.dumps(
            dict(
                purpose="Runtime compatibility only; no VLM API, ground truth or accuracy claim",
                records=records,
            ),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
