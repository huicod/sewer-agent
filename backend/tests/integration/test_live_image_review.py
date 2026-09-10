"""Opt-in original-image Qwen review using a previously saved real detector batch."""

import json
import os
from pathlib import Path

import pytest
from pipe_inspector.agent.api_client import OpenAICompatibleClient
from pipe_inspector.agent.image_review import ImageReviewer
from pipe_inspector.config import Settings
from pipe_inspector.contracts.detection import DetectionBatch
from pipe_inspector.contracts.inspection import ImageObservation
from pipe_inspector.vision.quality import analyze_quality


@pytest.mark.live_api
@pytest.mark.skipif(
    os.environ.get("SEWER_RUN_LIVE_API") != "1",
    reason="explicit opt-in: one external image review may be billed",
)
async def test_original_cctv_image_returns_validated_review():
    selected = os.environ.get("SEWER_LIVE_IMAGE")
    saved = os.environ.get("SEWER_LIVE_DETECTIONS")
    assert selected and saved, "Explicitly select both image and saved detector result"
    picture = Path(selected)
    metadata = json.loads(Path(saved).read_text())
    assert Path(metadata["image"]).resolve() == picture.resolve(), "Image/result mismatch"
    batch = DetectionBatch.model_validate(metadata["result"])
    settings = Settings()
    assert settings.vlm_configured, "Configure VLM locally first"
    client = OpenAICompatibleClient(
        settings.vlm_base_url,
        settings.vlm_model,
        settings.vlm_api_key,
        timeout_seconds=settings.api_timeout_seconds,
        enable_thinking=settings.api_enable_thinking,
        max_output_tokens=settings.api_max_output_tokens,
    )
    observation = ImageObservation(detection=batch, quality=analyze_quality(picture))
    result = await ImageReviewer(client, settings.vlm_max_image_bytes).review(picture, observation)
    assert len(result.candidates) == len(batch.detections)
    # This validates integration and evidence alignment, never model accuracy.
    print(
        json.dumps(
            {"model": settings.vlm_model, "review": result.model_dump(mode="json")},
            ensure_ascii=False,
        )
    )
