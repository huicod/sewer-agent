"""Safety boundaries for the explicitly paid/model-backed browser harness."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from browser_live import (
    LIVE_API_LIMIT,
    GpuExecutionRecorder,
    LiveExecutionRecorder,
    build_live_components,
    copy_live_fixture,
    load_live_admission,
)
from PIL import Image
from pipe_inspector.agent.api_client import APIClientError
from pipe_inspector.config import Settings
from pipe_inspector.media import MediaStore
from pydantic import BaseModel, SecretStr


class _Answer(BaseModel):
    value: int


def _response(*, usage=...):
    envelope = {
        "choices": [
            {
                "message": {"content": json.dumps({"value": 7})},
                "finish_reason": "stop",
            }
        ]
    }
    if usage is not ...:
        envelope["usage"] = usage
    return httpx.Response(200, json=envelope)


def _factory(handler):
    return lambda: httpx.MockTransport(handler)


def _client(recorder, handler, *, key="paid-secret"):
    return recorder.client(
        role="intent",
        base_url="https://provider.example/v1",
        model="qwen-test",
        api_key=SecretStr(key),
        timeout_seconds=1,
        transport_factory=_factory(handler),
    )


def test_live_admission_requires_both_paid_gates_and_absolute_existing_image(tmp_path):
    source = tmp_path / "source.jpg"
    Image.new("RGB", (720, 576)).save(source)

    assert load_live_admission({}) is None
    assert (
        load_live_admission(
            {"SEWER_RUN_BROWSER_LIVE": "0", "SEWER_RUN_LIVE_API": "0"}
        )
        is None
    )
    for values in (
        {"SEWER_RUN_BROWSER_LIVE": "1"},
        {"SEWER_RUN_LIVE_API": "1"},
        {
            "SEWER_RUN_BROWSER_LIVE": "1",
            "SEWER_RUN_LIVE_API": "1",
            "SEWER_LIVE_IMAGE": "relative.jpg",
        },
    ):
        with pytest.raises(RuntimeError, match="live browser"):
            load_live_admission(values)

    admission = load_live_admission(
        {
            "SEWER_RUN_BROWSER_LIVE": "1",
            "SEWER_RUN_LIVE_API": "1",
            "SEWER_LIVE_IMAGE": str(source),
        }
    )
    assert admission is not None and admission.source == source


def test_live_admission_rejects_incompatible_fixed_roi_image_before_paid_work(tmp_path):
    source = tmp_path / "small.jpg"
    Image.new("RGB", (8, 6)).save(source)
    with pytest.raises(RuntimeError, match="720x576"):
        load_live_admission({
            "SEWER_RUN_BROWSER_LIVE": "1",
            "SEWER_RUN_LIVE_API": "1",
            "SEWER_LIVE_IMAGE": str(source),
        })


def test_live_fixture_keeps_real_jpeg_bytes_without_persisting_source_path(tmp_path):
    source = tmp_path / "private-name.jpg"
    Image.new("RGB", (9, 7), color=(20, 30, 40)).save(source, quality=91)
    output = tmp_path / "output"
    output.mkdir()

    metadata = copy_live_fixture(source, output)

    fixture = output / "fixtures" / "image.jpg"
    assert fixture.read_bytes() == source.read_bytes()
    assert metadata == {
        "fixture": "fixtures/image.jpg",
        "media_type": "image/jpeg",
        "byte_size": fixture.stat().st_size,
        "width": 9,
        "height": 7,
    }
    assert str(source) not in json.dumps(metadata)


def test_recording_client_preserves_usage_zero_and_marks_missing_usage_unknown():
    responses = iter(
        [
            _response(
                usage={
                    "prompt_tokens": 12,
                    "completion_tokens": 3,
                    "total_tokens": 15,
                    "prompt_tokens_details": {"cached_tokens": 4},
                }
            ),
            _response(usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}),
            _response(),
        ]
    )
    recorder = LiveExecutionRecorder()
    client = _client(recorder, lambda request: next(responses))

    async def run():
        for _ in range(3):
            assert await client.generate([{"role": "user", "content": "safe"}], _Answer)

    asyncio.run(run())

    calls = recorder.snapshot()["provider_calls"]
    assert calls[0]["usage"] == {
        "prompt_tokens": 12,
        "completion_tokens": 3,
        "total_tokens": 15,
        "cached_tokens": 4,
    }
    assert calls[1]["usage"] == {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }
    assert calls[2]["usage"] is None
    assert recorder.snapshot()["usage_unknown_calls"] == 1


def test_live_api_budget_counts_failures_and_does_not_send_ninth_request():
    sent = 0

    def timeout(request):
        nonlocal sent
        sent += 1
        raise httpx.ReadTimeout("private upstream body", request=request)

    recorder = LiveExecutionRecorder()
    client = _client(recorder, timeout)

    async def run():
        for _ in range(LIVE_API_LIMIT):
            with pytest.raises(APIClientError, match="timeout"):
                await client.generate([{"role": "user", "content": "inspect"}], _Answer)
        with pytest.raises(APIClientError) as caught:
            await client.generate([{"role": "user", "content": "inspect"}], _Answer)
        assert caught.value.code == "live_api_budget_exhausted"
        assert caught.value.retryable is False

    asyncio.run(run())

    snapshot = recorder.snapshot()
    assert sent == LIVE_API_LIMIT
    assert len(snapshot["provider_calls"]) == LIVE_API_LIMIT
    assert snapshot["failed_calls"] == LIVE_API_LIMIT
    assert snapshot["budget_rejections"] == 1


def test_live_record_never_contains_credentials_base64_or_full_provider_error():
    secret = "paid-secret-never-save"
    image = "data:image/jpeg;base64,THIS_IS_PRIVATE_MEDIA"

    def fail(request):
        assert request.headers["authorization"] == f"Bearer {secret}"
        return httpx.Response(500, text="provider-private-error-body")

    recorder = LiveExecutionRecorder()
    client = _client(recorder, fail, key=secret)
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "inspect this image"},
                {"type": "image_url", "image_url": {"url": image}},
            ],
        }
    ]

    with pytest.raises(APIClientError, match="upstream_error"):
        asyncio.run(client.generate(messages, _Answer))

    serialized = json.dumps(recorder.snapshot())
    assert "inspect this image" in serialized
    assert "[image omitted]" in serialized
    for private in (secret, "THIS_IS_PRIVATE_MEDIA", "provider-private-error-body"):
        assert private not in serialized


def test_gpu_recorder_normalizes_ultralytics_numeric_device(monkeypatch):
    observed = []

    class FakeCuda:
        @staticmethod
        def is_available():
            return True

        @staticmethod
        def synchronize(device):
            observed.append(("synchronize", device))

        @staticmethod
        def reset_peak_memory_stats(device):
            observed.append(("reset", device))

        @staticmethod
        def max_memory_allocated(device):
            observed.append(("allocated", device))
            return 12

        @staticmethod
        def max_memory_reserved(device):
            observed.append(("reserved", device))
            return 34

    monkeypatch.setitem(__import__("sys").modules, "torch", SimpleNamespace(cuda=FakeCuda()))

    recorder = GpuExecutionRecorder()
    assert recorder.measure("detect", "0", lambda: "ok") == "ok"

    assert {device for _, device in observed} == {0}
    assert recorder.snapshot()["peak_allocated_bytes"] == 12
    assert recorder.snapshot()["peak_reserved_bytes"] == 34


def test_live_components_construct_production_adapters_without_loading_or_requests(tmp_path):
    settings = Settings(
        _env_file=None,
        asset_root=tmp_path / "assets",
        detector_path=tmp_path / "detector.pt",
        detector_device="0",
        llm_base_url="https://provider.example/v1",
        llm_model="qwen-intent",
        llm_api_key="test-only",
        vlm_base_url="https://provider.example/v1",
        vlm_model="qwen-review",
        vlm_api_key="test-only",
    )

    components = build_live_components(settings, object(), MediaStore(settings.asset_root))

    assert components.detector.detector_id == "ultralytics-yolo26"
    assert components.models["detector"] == "detector.pt"
    assert components.models["intent"] == "qwen-intent"
    assert components.models["review"] == "qwen-review"
    from pipe_inspector.agent.video_selection import VideoSceneSelector

    assert isinstance(components.videos.scene_selector, VideoSceneSelector)
    assert components.recorder.snapshot()["provider_calls"] == []
    assert components.gpu.snapshot()["serial_calls"] == []
