"""Explicit paid/API checks; ordinary pytest runs never send media or API requests."""

import base64
import os
from pathlib import Path
from typing import Literal

import pytest
from PIL import Image
from pipe_inspector.agent.api_client import OpenAICompatibleClient
from pipe_inspector.config import Settings
from pydantic import BaseModel, Field


class SmokeResponse(BaseModel):
    acknowledgement: Literal["sewer-agent"]
    observation: str = Field(min_length=1)


@pytest.mark.live_api
@pytest.mark.skipif(
    os.environ.get("SEWER_RUN_LIVE_API") != "1",
    reason="explicit opt-in required: external calls may be billed and VLM sends a selected image",
)
@pytest.mark.parametrize("kind", ["llm", "vlm"])
async def test_configured_provider_returns_structured_result(kind: str):
    settings = Settings()
    assert getattr(settings, f"{kind}_configured"), (
        f"Configure SEWER_{kind.upper()}_* locally first"
    )
    content: str | list[dict] = (
        'Return JSON with acknowledgement="sewer-agent" and a brief observation about this test.'
    )
    if kind == "vlm":
        selected = os.environ.get("SEWER_LIVE_IMAGE")
        assert selected, "Set SEWER_LIVE_IMAGE to the one image explicitly selected for API upload"
        path = Path(selected)
        with Image.open(path) as picture:
            mime = {"JPEG": "image/jpeg", "PNG": "image/png"}.get(picture.format)
            assert mime, "The live smoke test accepts a JPEG or PNG image"
            picture.verify()
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        content = [
            {
                "type": "text",
                "text": (
                    "Describe what is visible in this image. Return JSON with "
                    'acknowledgement="sewer-agent" and observation containing your description.'
                ),
            },
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
        ]
    client = OpenAICompatibleClient(
        base_url=getattr(settings, f"{kind}_base_url"),
        model=getattr(settings, f"{kind}_model"),
        api_key=getattr(settings, f"{kind}_api_key"),
        timeout_seconds=settings.api_timeout_seconds,
        enable_thinking=settings.api_enable_thinking,
        max_output_tokens=settings.api_max_output_tokens,
    )
    result = await client.generate([{"role": "user", "content": content}], SmokeResponse)
    assert result.acknowledgement == "sewer-agent"
    # Protocol compatibility only: a valid response does not demonstrate defect accuracy.
    assert result.observation.strip()
