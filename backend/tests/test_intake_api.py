"""HTTP admission limits and schema guards must act before repository operations."""

import pytest
from httpx import ASGITransport, AsyncClient
from pipe_inspector.api.app import create_app
from pipe_inspector.config import Settings
from pydantic import ValidationError


def test_upload_limits_must_be_positive():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, max_upload_bytes=0)


async def test_oversized_declared_body_is_rejected_without_reading_database(tmp_path):
    app = create_app(Settings(_env_file=None, asset_root=tmp_path, max_upload_bytes=10))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        result = await client.post("/api/assets", content=b"", headers={"content-length": "999999"})
    assert result.status_code == 413


async def test_chunked_oversized_body_is_stopped_during_multipart_receive(tmp_path):
    app = create_app(Settings(_env_file=None, asset_root=tmp_path, max_upload_bytes=10))

    async def chunks():
        yield b'--sample\r\nContent-Disposition: form-data; name="file"; filename="x"\r\n\r\n'
        yield b"x" * 70_000
        yield b"\r\n--sample--\r\n"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        result = await client.post(
            "/api/assets",
            content=chunks(),
            headers={"content-type": "multipart/form-data; boundary=sample"},
        )
    assert result.status_code == 413


async def test_paths_and_client_runtime_status_are_not_task_inputs(tmp_path):
    app = create_app(Settings(_env_file=None, asset_root=tmp_path))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        result = await client.post(
            "/api/tasks",
            json={
                "path": "/etc/passwd",
                "runtime_status": "completed",
                "instruction": "inspect",
            },
        )
    assert result.status_code == 422


async def test_unavailable_storage_returns_safe_error_not_credentials(tmp_path):
    app = create_app(
        Settings(
            _env_file=None,
            asset_root=tmp_path,
            database_dsn="postgresql://private:secret@127.0.0.1:1/missing?connect_timeout=1",
        )
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        result = await client.post("/api/sessions")
    assert result.status_code == 503
    assert "private" not in result.text
    assert "secret" not in result.text


async def test_file_limit_applies_even_within_multipart_overhead_allowance(tmp_path):
    app = create_app(Settings(_env_file=None, asset_root=tmp_path, max_upload_bytes=10))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        result = await client.post("/api/assets", files={"file": ("image.png", b"x" * 11)})
    assert result.status_code == 413
    assert not list(tmp_path.rglob("source"))


async def test_multipart_rejects_extra_files_and_missing_file(tmp_path):
    app = create_app(Settings(_env_file=None, asset_root=tmp_path))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        extra = await client.post(
            "/api/assets",
            files=[
                ("file", ("one.png", b"one")),
                ("file", ("two.png", b"two")),
            ],
        )
        missing = await client.post("/api/assets")
    assert extra.status_code == 400
    assert extra.json()["detail"]["code"] == "invalid_request"
    assert isinstance(extra.json()["detail"]["message"], str)
    assert missing.status_code == 422


async def test_chunked_json_limit_does_not_reach_task_creation(tmp_path):
    app = create_app(Settings(_env_file=None, asset_root=tmp_path))

    async def chunks():
        yield b'{"instruction":"'
        yield b"x" * 70_000
        yield b'"}'

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        result = await client.post(
            "/api/tasks", content=chunks(), headers={"content-type": "application/json"}
        )
    assert result.status_code == 413


@pytest.mark.parametrize("length", ["bad", "-1"])
async def test_invalid_content_length_uses_structured_error(tmp_path, length):
    app = create_app(Settings(_env_file=None, asset_root=tmp_path))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        result = await client.post("/api/assets", content=b"", headers={"content-length": length})
    assert result.status_code == 400
    assert result.json()["detail"]["code"] == "invalid_request"
    assert isinstance(result.json()["detail"]["message"], str)


async def test_malformed_multipart_uses_structured_error(tmp_path):
    app = create_app(Settings(_env_file=None, asset_root=tmp_path))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        result = await client.post(
            "/api/assets", content=b"bad", headers={"content-type": "multipart/form-data"}
        )
    assert result.status_code == 400
    assert result.json()["detail"]["code"] == "invalid_request"
