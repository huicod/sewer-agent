"""The built web app is opt-in and must not hide API routes or expose source files."""

import pytest
from httpx import ASGITransport, AsyncClient
from pipe_inspector.api.app import create_app
from pipe_inspector.config import Settings


@pytest.mark.asyncio
async def test_build_is_served_on_api_origin_without_exposing_parent_files(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<h1>fixture</h1>")
    (dist / "assets").mkdir()
    (dist / "assets/app.js").write_text("console.log('fixture')")
    (tmp_path / ".env").write_text("private-key")
    (dist / "escape").symlink_to(tmp_path / ".env")
    settings = Settings(_env_file=None, web_dist=dist, asset_root=tmp_path / "media")
    async with AsyncClient(
        transport=ASGITransport(app=create_app(settings)), base_url="http://test"
    ) as client:
        response = await client.get("/")
        assert response.status_code == 200
        assert response.text == "<h1>fixture</h1>"
        assert (await client.get("/assets/app.js")).text == "console.log('fixture')"
        assert (await client.get("/health")).json()["status"] == "alive"
        for path in ("/api/not-a-route", "/.env", "/escape", "/%2e%2e/.env"):
            response = await client.get(path)
            assert response.status_code == 404
            assert "private-key" not in response.text
            assert "<h1>fixture</h1>" not in response.text


@pytest.mark.asyncio
async def test_api_only_remains_default(tmp_path):
    settings = Settings(_env_file=None, asset_root=tmp_path)
    async with AsyncClient(
        transport=ASGITransport(app=create_app(settings)), base_url="http://test"
    ) as client:
        assert (await client.get("/")).status_code == 404
        assert (await client.get("/health")).status_code == 200


def test_selected_web_build_requires_index_before_serving(tmp_path):
    with pytest.raises(RuntimeError, match="build"):
        create_app(Settings(_env_file=None, web_dist=tmp_path, asset_root=tmp_path / "media"))
