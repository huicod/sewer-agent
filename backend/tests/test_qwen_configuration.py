import json

import httpx
import pytest
from pipe_inspector.agent.api_client import OpenAICompatibleClient
from pipe_inspector.config import Settings
from pydantic import BaseModel, SecretStr

BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def test_dashscope_key_is_usable_for_explicit_official_qwen_configuration():
    settings = Settings(
        _env_file=None,
        DASHSCOPE_API_KEY="fixture-shared-secret",
        vlm_base_url=BASE_URL,
        vlm_model="qwen3.8-max",
    )
    assert settings.vlm_configured
    assert settings.vlm_api_key.get_secret_value() == "fixture-shared-secret"
    assert not settings.llm_configured
    assert "fixture-shared-secret" not in repr(settings)


@pytest.mark.parametrize(
    "url",
    [
        "https://other.example/v1",
        "http://dashscope.aliyuncs.com/v1",
        "https://dashscope.aliyuncs.com.attacker.invalid/v1",
    ],
)
def test_dashscope_fallback_is_never_sent_to_an_unrelated_provider(url):
    settings = Settings(
        _env_file=None,
        DASHSCOPE_API_KEY="fixture-shared-secret",
        vlm_base_url=url,
        vlm_model="qwen3.8-max",
    )
    assert not settings.vlm_configured


def test_explicit_role_key_takes_precedence_over_shared_key():
    settings = Settings(
        _env_file=None,
        DASHSCOPE_API_KEY="fixture-shared-secret",
        vlm_base_url=BASE_URL,
        vlm_model="qwen3.8-max",
        vlm_api_key="role-key",
    )
    assert settings.vlm_api_key.get_secret_value() == "role-key"


@pytest.mark.asyncio
async def test_structured_qwen_request_can_disable_thinking_and_bound_output():
    class Result(BaseModel):
        ok: bool

    def handle(request):
        payload = json.loads(request.content)
        assert payload["enable_thinking"] is False
        assert payload["max_tokens"] == 256
        return httpx.Response(
            200,
            json={"choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}]},
        )

    client = OpenAICompatibleClient(
        BASE_URL,
        "qwen3.8-max",
        SecretStr("fixture"),
        transport=httpx.MockTransport(handle),
        enable_thinking=False,
        max_output_tokens=256,
    )
    assert (await client.generate([{"role": "user", "content": "Return JSON"}], Result)).ok


def test_both_qwen_roles_can_share_one_key_with_pinned_request_limits():
    from pipe_inspector.workflow.activities import ImageActivities
    from pipe_inspector.workflow.start import run_config
    from temporalio.exceptions import ApplicationError

    settings = Settings(
        _env_file=None,
        DASHSCOPE_API_KEY="fixture-shared",
        llm_base_url=BASE_URL,
        llm_model="qwen3.8-max",
        vlm_base_url=BASE_URL,
        vlm_model="qwen3.8-max",
        api_enable_thinking=False,
        api_max_output_tokens=4096,
    )
    assert settings.llm_configured and settings.vlm_configured
    config = run_config(settings)
    assert config["api_enable_thinking"] is False
    assert config["api_max_output_tokens"] == 4096
    assert "fixture-shared" not in json.dumps(config)
    activities = ImageActivities(settings, None, None)
    activities._check_versions(config, vlm=True)
    with pytest.raises(ApplicationError, match="differs from pinned"):
        activities._check_versions(config | {"api_max_output_tokens": 256}, vlm=True)


@pytest.mark.parametrize("value", [0, -1, 8193, True, 1.5])
def test_invalid_output_cap_is_rejected_before_network(value):
    from pipe_inspector.agent.api_client import APIClientError

    with pytest.raises(APIClientError, match="invalid_configuration"):
        OpenAICompatibleClient(
            BASE_URL, "qwen3.8-max", SecretStr("fixture"), max_output_tokens=value
        )
