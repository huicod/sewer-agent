"""Structured-output client for explicitly OpenAI-compatible providers."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, SecretStr, ValidationError

ResponseModel = TypeVar("ResponseModel", bound=BaseModel)


class APIClientError(RuntimeError):
    """Safe failure metadata for Temporal retry policy decisions."""

    def __init__(
        self,
        code: str,
        *,
        retryable: bool,
        status_code: int | None = None,
    ) -> None:
        super().__init__(f"OpenAI-compatible API request failed ({code}).")
        self.code = code
        self.retryable = retryable
        self.status_code = status_code


class OpenAICompatibleClient:
    """Submit one non-streaming chat-completions request and validate its JSON result."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: SecretStr,
        timeout_seconds: float = 60,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        try:
            url = httpx.URL(base_url.strip())
        except (TypeError, ValueError):
            raise self._error("invalid_configuration") from None

        valid_url = (
            url.scheme in {"http", "https"}
            and bool(url.host)
            and not url.username
            and not url.password
        )
        valid_key = isinstance(api_key, SecretStr) and bool(api_key.get_secret_value().strip())
        if not valid_url or not model.strip() or not valid_key or timeout_seconds <= 0:
            raise self._error("invalid_configuration")

        self._endpoint = f"{str(url).rstrip('/')}/chat/completions"
        self._model = model.strip()
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def generate(
        self,
        messages: Sequence[Mapping[str, Any]],
        response_model: type[ResponseModel],
    ) -> ResponseModel:
        headers = {"Authorization": f"Bearer {self._api_key.get_secret_value()}"}
        payload = {
            "model": self._model,
            "messages": list(messages),
            "response_format": {"type": "json_object"},
        }

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                transport=self._transport,
                follow_redirects=False,
            ) as client:
                response = await client.post(self._endpoint, headers=headers, json=payload)
        except httpx.TimeoutException:
            raise self._error("timeout", retryable=True) from None
        except httpx.RequestError:
            raise self._error("network_error", retryable=True) from None
        except (TypeError, ValueError):
            raise self._error("invalid_request") from None

        self._raise_for_status(response)
        return self._parse_response(response, response_model)

    @classmethod
    def _raise_for_status(cls, response: httpx.Response) -> None:
        status = response.status_code
        if 200 <= status < 300:
            return
        if 300 <= status < 400:
            raise cls._error("redirect_not_allowed", status_code=status)
        if status == 429:
            raise cls._error("rate_limited", retryable=True, status_code=status)
        if status >= 500:
            raise cls._error("upstream_error", retryable=True, status_code=status)
        if status in {401, 403}:
            raise cls._error("authentication_failed", status_code=status)
        raise cls._error("request_rejected", status_code=status)

    @classmethod
    def _parse_response(
        cls,
        response: httpx.Response,
        response_model: type[ResponseModel],
    ) -> ResponseModel:
        if not response.content:
            raise cls._error("empty_response", status_code=response.status_code)
        try:
            envelope = response.json()
        except ValueError:
            raise cls._error("invalid_json", status_code=response.status_code) from None

        try:
            choice = envelope["choices"][0]
            message = choice["message"]
            content = message.get("content")
            refusal = message.get("refusal")
            finish_reason = choice.get("finish_reason")
        except (AttributeError, KeyError, IndexError, TypeError):
            raise cls._error("response_schema_invalid", status_code=response.status_code) from None

        if refusal:
            raise cls._error("response_refused", status_code=response.status_code)
        if finish_reason == "length":
            raise cls._error("response_truncated", status_code=response.status_code)
        if content is None or (isinstance(content, str) and not content.strip()):
            raise cls._error("empty_response", status_code=response.status_code)
        if not isinstance(content, str):
            raise cls._error("response_schema_invalid", status_code=response.status_code)

        try:
            structured_content = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            raise cls._error("invalid_json", status_code=response.status_code) from None
        try:
            return response_model.model_validate(structured_content)
        except (TypeError, ValidationError):
            raise cls._error("response_schema_invalid", status_code=response.status_code) from None

    @staticmethod
    def _error(
        code: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
    ) -> APIClientError:
        return APIClientError(code, retryable=retryable, status_code=status_code)
