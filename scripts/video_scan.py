#!/usr/bin/env python3
"""Explicit local video primary scan; durable web video execution belongs to T08."""

import argparse
import json
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from pipe_inspector.contracts.assets import AssetRef
from pipe_inspector.vision.detector import UltralyticsDetector
from pipe_inspector.vision.video_decode import VideoDecodeError
from pipe_inspector.vision.video_scan import extract_scan_evidence, scan_video


def run_scan(args, detector):
    args.output.mkdir(parents=True, exist_ok=False)
    started = perf_counter()
    source_ref = AssetRef(asset_id=uuid4())
    options = dict(
        chunk_size=args.chunk_size, timeout_seconds=args.timeout_seconds, max_pixels=args.max_pixels
    )
    with (args.output / "observations.jsonl").open("x", encoding="utf-8") as records:

        def save_chunk(observations, state):
            for observation in observations:
                records.write(observation.model_dump_json() + "\n")
            records.flush()

        result = scan_video(args.source, source_ref, detector, on_chunk=save_chunk, **options)
    evidence, evidence_error = (), None
    try:
        evidence = extract_scan_evidence(args.source, result, args.output / "frames", **options)
    except VideoDecodeError as exc:
        evidence_error = exc.code
    except OSError:
        evidence_error = "evidence_storage_failed"
    manifest = {
        "schema_version": "video-scan-v1",
        "scope": "primary_scan_only",
        "source_path": str(args.source.resolve()),
        "checkpoint": str(args.checkpoint),
        "device": args.device,
        "imgsz": args.imgsz,
        "conf": args.conf,
        "chunk_size": args.chunk_size,
        "scan": result.model_dump(mode="json"),
        "evidence": [item.model_dump(mode="json") for item in evidence],
        "evidence_error": evidence_error,
        "elapsed_seconds": perf_counter() - started,
    }
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="new output directory; never overwrites an earlier run",
    )
    parser.add_argument("--device", default="0")
    parser.add_argument("--imgsz", type=int, default=960)
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--chunk-size", type=int, default=8, choices=range(1, 33))
    parser.add_argument("--timeout-seconds", type=float, default=30)
    parser.add_argument("--max-pixels", type=int, default=40_000_000)
    args = parser.parse_args()
    try:
        detector = UltralyticsDetector(
            args.checkpoint, device=args.device, imgsz=args.imgsz, conf=args.conf
        )
        result = run_scan(args, detector)
    except (OSError, ValueError):
        raise SystemExit(
            "video scan could not start/write; check explicit paths and options"
        ) from None
    scan = result["scan"]
    print(
        json.dumps(
            {
                "scope": result["scope"],
                "processed_frames": scan["state"]["next_frame"],
                "events": len(scan["state"]["events"]),
                "decode_complete": scan["decode_complete"],
                "error_code": scan["error_code"],
                "evidence_error": result["evidence_error"],
            },
            ensure_ascii=False,
        )
    )
    if not scan["decode_complete"] or result["evidence_error"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
