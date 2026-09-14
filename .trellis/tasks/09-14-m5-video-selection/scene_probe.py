"""One explicit paid scene probe over 16 already-persisted T15-D PNGs."""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend/tests"))
from browser_live import LiveExecutionRecorder, _role_client  # noqa: E402
from pipe_inspector.agent.api_client import APIClientError  # noqa: E402
from pipe_inspector.agent.video_selection import VideoSceneSelector  # noqa: E402
from pipe_inspector.config import Settings  # noqa: E402


async def main():
    if os.getenv("SEWER_RUN_LIVE_API") != "1" or os.getenv("SEWER_RUN_VIDEO_COST_LIVE") != "1":
        raise RuntimeError("Both explicit paid-video gates are required")
    source = ROOT / "var/evaluations/2026-09-14-video-cost/candidate-30s"
    output = ROOT / "var/evaluations/2026-09-14-video-selection/scene-probe.json"
    if output.exists():
        raise RuntimeError("Existing scene probe must not be overwritten or rerun")
    evidence = json.loads((source / "video-cost-summary.json").read_text())
    frames = [
        (frame["frame_index"], source / frame["artifact"]) for frame in evidence["evidence_frames"]
    ]
    assert len(frames) == 16 and all(path.is_file() for _, path in frames)
    settings = Settings(api_max_output_tokens=4096, api_enable_thinking=False)
    recorder = LiveExecutionRecorder()
    selector = VideoSceneSelector(
        _role_client(settings, recorder, "video_scene"),
        max_image_bytes=settings.vlm_max_image_bytes,
    )
    started = time.perf_counter()
    result, error = None, None
    try:
        result = (await selector.classify(frames)).model_dump(mode="json")
    except APIClientError as exc:
        error = exc.code
    payload = {
        "source_summary": str((source / "video-cost-summary.json").relative_to(ROOT)),
        "frames": [
            {"frame_index": index, "artifact": str(path.relative_to(ROOT))}
            for index, path in frames
        ],
        "result": result,
        "error": error,
        "elapsed_seconds": time.perf_counter() - started,
        "provider": recorder.snapshot(),
        "scope": "Scene classification only; no new detector run or defect judgement",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps({"artifact": str(output), "error": error, "result": result}, ensure_ascii=False)
    )


if __name__ == "__main__":
    asyncio.run(main())
