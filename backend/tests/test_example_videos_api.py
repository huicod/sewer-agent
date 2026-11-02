import io
import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pipe_inspector.api.app import create_app
from pipe_inspector.config import Settings

EXAMPLE = {"id": "18_field_video", "title": "现场短片", "instruction": "检查这段视频"}


@pytest.fixture
def example_app(tmp_path):
    root = tmp_path / "examples"
    root.mkdir()
    settings = Settings(
        _env_file=None, example_root=root, asset_root=tmp_path / "assets", web_dist=None
    )
    repo = Mock()
    with TestClient(create_app(settings, repository=repo)) as client:
        yield client, root, repo


def test_selecting_bundled_video_creates_normal_asset_without_upload(example_app):
    client, root, repo = example_app
    (root / "field-videos.json").write_text(json.dumps([EXAMPLE]))
    clip = root / EXAMPLE["id"] / "clip.mp4"
    clip.parent.mkdir()
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=s=32x24:r=2",
            "-t", "1", "-c:v", "mpeg4", str(clip),
        ],
        check=True,
        timeout=20,
    )
    assert client.get("/api/example-videos").json() == [EXAMPLE]
    result = client.post(f"/api/example-videos/{EXAMPLE['id']}/asset")
    assert result.status_code == 201
    record = repo.save_asset.call_args.args[0]
    assert result.json() == record.model_dump(mode="json")
    assert (record.kind, record.media_type, record.width, record.height) == (
        "video", "video/mp4", 32, 24,
    )
    assert record.duration_seconds == pytest.approx(1, abs=0.05)
    repo.get_asset.return_value = record
    assert client.get(
        f"/api/assets/{record.ref.asset_id}/versions/{record.ref.version}/content"
    ).content == clip.read_bytes()


def test_missing_catalog_does_not_affect_regular_upload(example_app):
    client, _, repo = example_app
    assert client.get("/api/example-videos").json() == []
    assert client.post(f"/api/example-videos/{EXAMPLE['id']}/asset").status_code == 404
    image = io.BytesIO()
    Image.new("RGB", (2, 2)).save(image, format="PNG")
    uploaded = client.post("/api/assets", files={"file": ("pipe.png", image.getvalue())})
    assert uploaded.status_code == 201
    repo.save_asset.assert_called_once()


def test_import_only_serves_listed_regular_files(example_app, tmp_path):
    client, root, repo = example_app
    (root / "field-videos.json").write_text(json.dumps([EXAMPLE]))
    outside = tmp_path / "private"
    outside.mkdir()
    (outside / "clip.mp4").write_bytes(b"unrelated data")
    path = root / EXAMPLE["id"]
    url = f"/api/example-videos/{EXAMPLE['id']}/asset"
    assert client.post(url).status_code == 404
    (root / "99_field_unlisted").symlink_to(outside, target_is_directory=True)
    assert client.post("/api/example-videos/99_field_unlisted/asset").status_code == 404
    path.symlink_to(outside, target_is_directory=True)
    assert client.post(url).status_code == 404
    path.unlink()
    path.mkdir()
    (path / "clip.mp4").symlink_to(outside / "clip.mp4")
    assert client.post(url).status_code == 404
    assert client.post("/api/example-videos/..%2Fprivate/asset").status_code == 404
    repo.save_asset.assert_not_called()


def test_catalog_rejects_paths_instead_of_ids(example_app):
    client, root, repo = example_app
    (root / "field-videos.json").write_text(json.dumps([EXAMPLE | {"id": "../private"}]))
    response = client.get("/api/example-videos")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "example_catalog_invalid"
    repo.save_asset.assert_not_called()


def test_shipped_catalog_has_media_for_every_selectable_example():
    root = Path(__file__).resolve().parents[2] / "example"
    app = create_app(Settings(_env_file=None, example_root=root))
    with TestClient(app) as client:
        response = client.get("/api/example-videos")
    assert response.status_code == 200
    videos = response.json()
    assert any(video["id"] == "18_field_video" for video in videos)
    for video in videos:
        assert (root / video["id"] / "clip.mp4").is_file()
