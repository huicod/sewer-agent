"""Catch missing health metadata and accidental secret disclosure in config."""

from httpx import ASGITransport, AsyncClient


async def test_health_identifies_service_without_claiming_dependencies_are_ready():
    from pipe_inspector.api.app import create_app

    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"service": "sewer-agent", "status": "alive", "version": "0.1.0"}


def test_settings_do_not_expose_api_keys_in_repr():
    from pipe_inspector.config import Settings

    settings = Settings(llm_api_key="test-llm-credential", vlm_api_key="test-vlm-credential")
    assert "test-llm-credential" not in repr(settings)
    assert "test-vlm-credential" not in repr(settings)


def test_api_models_are_not_silently_assumed_configured():
    from pipe_inspector.config import Settings

    settings = Settings(_env_file=None, llm_api_key=None, vlm_api_key=None)
    assert not settings.llm_configured
    assert not settings.vlm_configured
