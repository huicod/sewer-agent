"""Real HTTP/media/PostgreSQL boundary in a uniquely owned disposable schema."""

import io
import os
import subprocess
from uuid import uuid4

import psycopg
import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image
from pipe_inspector.api.app import create_app
from pipe_inspector.config import Settings
from psycopg import sql

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("SEWER_RUN_STORAGE_TEST") != "1",
        reason="opt-in real storage integration",
    ),
]


@pytest.fixture
def api_store(tmp_path):
    from pipe_inspector.storage.repository import PostgresRepository

    settings = Settings(asset_root=tmp_path / "assets")
    schema = "test_intake_" + uuid4().hex
    with psycopg.connect(settings.database_dsn.get_secret_value()) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        repo = PostgresRepository(settings.database_dsn, schema=schema)
        repo.initialize()
        yield settings, repo, schema
    finally:
        with psycopg.connect(settings.database_dsn.get_secret_value()) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


async def test_upload_submit_reconnect_and_download_original(api_store):
    from pipe_inspector.storage.repository import PostgresRepository

    settings, repo, schema = api_store
    stream = io.BytesIO()
    Image.new("RGB", (24, 16), "blue").save(stream, format="PNG")
    original = stream.getvalue()
    async with AsyncClient(
        transport=ASGITransport(app=create_app(settings, repository=repo)),
        base_url="http://test",
    ) as client:
        session = await client.post("/api/sessions")
        assert session.status_code == 201
        uploaded = await client.post(
            "/api/assets",
            files={
                "file": ("../../not-a-video.mp4", original, "video/mp4"),
            },
        )
        assert uploaded.status_code == 201
        asset = uploaded.json()
        assert asset["media_type"] == "image/png"
        payload = dict(
            request_id=str(uuid4()),
            session_id=session.json()["session_id"],
            input_asset=asset["ref"],
            instruction="检查这张图片，关注破裂",
        )
        task = await client.post("/api/tasks", json=payload)
        assert task.status_code == 201
        record = task.json()
        assert record["snapshot"]["runtime_status"] == "queued"
        assert record["snapshot"]["inspection_result"] == "pending"
        assert record["snapshot"]["findings"] == []
        repeat = await client.post("/api/tasks", json=payload)
        assert repeat.json() == record
        conflict = await client.post("/api/tasks", json={**payload, "instruction": "different"})
        assert conflict.status_code == 409
    # A new app and repository know only IDs; no process-local task state survives.
    reopened = create_app(
        settings, repository=PostgresRepository(settings.database_dsn, schema=schema)
    )
    async with AsyncClient(transport=ASGITransport(app=reopened), base_url="http://test") as client:
        history = await client.get(f"/api/sessions/{payload['session_id']}")
        assert history.json()["tasks"] == [record]
        task = await client.get(f"/api/tasks/{record['snapshot']['task_id']}")
        assert task.json() == record
        prefix = f"/api/assets/{asset['ref']['asset_id']}/versions/1"
        assert (await client.get(prefix)).json() == asset
        content = await client.get(prefix + "/content")
        assert content.content == original
        assert content.headers["content-type"] == "image/png"
        assert (await client.get(f"/api/tasks/{uuid4()}")).status_code == 404
        assert (await client.get(f"/api/sessions/{uuid4()}")).status_code == 404
        assert (await client.get(f"/api/assets/{uuid4()}/versions/1/content")).status_code == 404


async def test_patch_session_metadata_and_validation(api_store):
    from pipe_inspector.storage.human_review import HumanReviewRepository

    settings, _, schema = api_store
    repo = HumanReviewRepository(settings.database_dsn, schema=schema)
    repo.initialize()
    async with AsyncClient(
        transport=ASGITransport(app=create_app(settings, repository=repo)),
        base_url="http://test",
    ) as client:
        created = (await client.post("/api/sessions")).json()
        path = f"/api/sessions/{created['session_id']}"
        updated = await client.patch(
            path, json={"title": "  项目 A  ", "pinned": True, "archived": True}
        )
        assert updated.status_code == 200
        assert updated.json()["custom_title"] == "项目 A"
        assert updated.json()["title"] == "项目 A"
        assert updated.json()["pinned"] is True
        assert updated.json()["archived"] is True

        partial = await client.patch(path, json={"archived": False})
        assert partial.json()["custom_title"] == "项目 A"
        assert partial.json()["pinned"] is True
        assert partial.json()["archived"] is False
        cleared = await client.patch(path, json={"title": None})
        assert cleared.json()["custom_title"] is None
        assert cleared.json()["title"] == "新对话"

        invalid = (
            {},
            {"title": "   "},
            {"pinned": None},
            {"pinned": "false"},
            {"archived": 1},
            {"unknown": True},
        )
        for payload in invalid:
            assert (await client.patch(path, json=payload)).status_code == 422
        missing = await client.patch(f"/api/sessions/{uuid4()}", json={"pinned": True})
        assert missing.status_code == 404


async def test_invalid_and_real_video_upload(api_store, tmp_path):
    settings, repo, _ = api_store
    clip = tmp_path / "input.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=s=32x24:r=5",
            "-t",
            "1",
            "-c:v",
            "mpeg4",
            str(clip),
        ],
        check=True,
        timeout=20,
    )
    async with AsyncClient(
        transport=ASGITransport(app=create_app(settings, repository=repo)),
        base_url="http://test",
    ) as client:
        bad = await client.post("/api/assets", files={"file": ("photo.jpg", b"bad", "image/jpeg")})
        assert bad.status_code == 415
        with clip.open("rb") as stream:
            good = await client.post(
                "/api/assets", files={"file": ("clip.mp4", stream, "video/mp4")}
            )
        assert good.status_code == 201
        assert good.json()["kind"] == "video"
