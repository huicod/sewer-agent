"""Contract tests for the OpenAI-compatible structured-output adapter."""

from typing import Any

import httpx
import pytest
from pipe_inspector.agent.api_client import APIClientError, OpenAICompatibleClient
from pydantic import BaseModel, SecretStr


class ReviewResult(BaseModel):
    decision: str
    needs_human_review: bool


def response_payload(
    content: str | None = '{"decision":"accept","needs_human_review":false}',
    *,
    finish_reason: str = "stop",
    refusal: str | None = None,
) -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {"content": content, "refusal": refusal},
                "finish_reason": finish_reason,
            }
        ]
    }


def make_client(handler: httpx.MockTransport) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        base_url="https://provider.example/v1/",
        model="vision-model",
        api_key=SecretStr("top-secret-key"),
        timeout_seconds=7,
        transport=handler,
    )


@pytest.mark.asyncio
async def test_generate_posts_expected_request_and_validates_schema() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url == httpx.URL("https://provider.example/v1/chat/completions")
        assert request.headers["authorization"] == "Bearer top-secret-key"
        assert request.headers["content-type"].startswith("application/json")
        assert request.read().decode() == (
            '{"model":"vision-model","messages":[{"role":"user","content":"inspect"}],'
            '"response_format":{"type":"json_object"}}'
        )
        return httpx.Response(200, json=response_payload())

    client = make_client(httpx.MockTransport(handler))

    result = await client.generate(
        [{"role": "user", "content": "inspect"}], response_model=ReviewResult
    )

    assert result == ReviewResult(decision="accept", needs_human_review=False)


@pytest.mark.parametrize(
    ("base_url", "model", "api_key"),
    [
        ("ftp://provider.example/v1", "model", "key"),
        ("https:///v1", "model", "key"),
        ("https://user:password@provider.example/v1", "model", "key"),
        ("https://provider.example/v1", " ", "key"),
        ("https://provider.example/v1", "model", ""),
    ],
)
def test_invalid_configuration_is_safe_and_non_retryable(
    base_url: str, model: str, api_key: str
) -> None:
    with pytest.raises(APIClientError) as caught:
        OpenAICompatibleClient(base_url, model, SecretStr(api_key))

    assert caught.value.code == "invalid_configuration"
    assert caught.value.retryable is False
    assert caught.value.status_code is None
    assert "password" not in str(caught.value)
    assert "key" not in str(caught.value)


@pytest.mark.asyncio
async def test_timeout_is_retryable_and_does_not_retry_in_client() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("unsafe timeout detail", request=request)

    client = make_client(httpx.MockTransport(handler))

    with pytest.raises(APIClientError) as caught:
        await client.generate([{"role": "user", "content": "private prompt"}], ReviewResult)

    assert attempts == 1
    assert caught.value.code == "timeout"
    assert caught.value.retryable is True
    assert caught.value.status_code is None
    assert "private prompt" not in str(caught.value)


@pytest.mark.asyncio
async def test_network_error_is_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unsafe network detail", request=request)

    with pytest.raises(APIClientError) as caught:
        await make_client(httpx.MockTransport(handler)).generate([], ReviewResult)

    assert (caught.value.code, caught.value.retryable) == ("network_error", True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (429, "rate_limited", True),
        (503, "upstream_error", True),
        (401, "authentication_failed", False),
        (403, "authentication_failed", False),
        (400, "request_rejected", False),
    ],
)
async def test_http_statuses_are_categorized_without_remote_body(
    status: int, code: str, retryable: bool
) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(status, text="remote-body-must-stay-private")
    )

    with pytest.raises(APIClientError) as caught:
        await make_client(transport).generate([], ReviewResult)

    assert caught.value.code == code
    assert caught.value.retryable is retryable
    assert caught.value.status_code == status
    assert "remote-body-must-stay-private" not in str(caught.value)
    assert "top-secret-key" not in str(caught.value)


@pytest.mark.asyncio
async def test_redirect_is_not_followed_with_bearer_credentials() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(307, headers={"location": "https://elsewhere.example/v1"})

    with pytest.raises(APIClientError) as caught:
        await make_client(httpx.MockTransport(handler)).generate([], ReviewResult)

    assert attempts == 1
    assert caught.value.code == "redirect_not_allowed"
    assert caught.value.retryable is False
    assert caught.value.status_code == 307


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "expected_code"),
    [
        (response_payload("not-json"), "invalid_json"),
        (response_payload('{"decision":3}'), "response_schema_invalid"),
        (response_payload(None), "empty_response"),
        (response_payload("  "), "empty_response"),
        (response_payload(refusal="policy refusal"), "response_refused"),
        (response_payload(finish_reason="length"), "response_truncated"),
        ({"choices": []}, "response_schema_invalid"),
        ({"choices": [{"message": [], "finish_reason": "stop"}]}, "response_schema_invalid"),
    ],
)
async def test_invalid_model_responses_are_non_retryable_and_safe(
    payload: dict[str, Any], expected_code: str
) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))

    with pytest.raises(APIClientError) as caught:
        await make_client(transport).generate(
            [{"role": "user", "content": "private prompt"}], ReviewResult
        )

    assert caught.value.code == expected_code
    assert caught.value.retryable is False
    assert caught.value.status_code == 200
    safe_text = str(caught.value)
    assert "private prompt" not in safe_text
    assert "policy refusal" not in safe_text
    assert "top-secret-key" not in safe_text
