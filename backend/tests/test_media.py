"""Admission must preserve originals and reject bad/unsafe media before queuing."""

import io
import subprocess
from uuid import uuid4

import pytest
from PIL import Image


def image_bytes(format="PNG"):
    stream = io.BytesIO()
    Image.new("RGB", (24, 16), "navy").save(stream, format=format)
    return stream.getvalue()


@pytest.mark.parametrize("format,mime", [("PNG", "image/png"), ("JPEG", "image/jpeg")])
def test_image_content_is_preserved_and_path_is_server_owned(tmp_path, format, mime):
    from pipe_inspector.media import MediaStore

    store = MediaStore(tmp_path)
    original = image_bytes(format)
    record = store.ingest(io.BytesIO(original))
    assert (record.kind, record.media_type, record.width, record.height) == ("image", mime, 24, 16)
    assert store.path_for(record.ref).read_bytes() == original
    assert record.byte_size == len(original)
    assert store.path_for(record.ref).parent.name == "1"


@pytest.mark.parametrize("data", [b"", b"not media", image_bytes()[:35], image_bytes("GIF")])
def test_invalid_media_is_rejected_without_leftover_file(tmp_path, data):
    from pipe_inspector.media import MediaError, MediaStore

    with pytest.raises(MediaError) as caught:
        MediaStore(tmp_path).ingest(io.BytesIO(data))
    assert caught.value.status_code == 415
    assert not list(tmp_path.rglob("source"))


def test_byte_limit_and_pixel_limit_reject_before_persistence(tmp_path):
    from pipe_inspector.media import MediaError, MediaStore

    with pytest.raises(MediaError) as caught:
        MediaStore(tmp_path, max_upload_bytes=10).ingest(io.BytesIO(image_bytes()))
    assert caught.value.status_code == 413
    with pytest.raises(MediaError) as caught:
        MediaStore(tmp_path, max_pixels=100).ingest(io.BytesIO(image_bytes()))
    assert caught.value.status_code == 413
    assert not list(tmp_path.rglob("source"))


@pytest.mark.parametrize(
    "extension,mime",
    [
        ("mp4", "video/mp4"),
        ("mov", "video/quicktime"),
        ("avi", "video/x-msvideo"),
        ("mkv", "video/x-matroska"),
    ],
)
def test_real_video_metadata_and_first_frame_are_verified(tmp_path, extension, mime):
    from pipe_inspector.media import MediaStore

    clip = tmp_path / f"sample.{extension}"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=32x24:r=5",
            "-t",
            "1",
            "-c:v",
            "mpeg4",
            str(clip),
        ],
        check=True,
        timeout=20,
    )
    with clip.open("rb") as stream:
        record = MediaStore(tmp_path / "assets").ingest(stream)
    assert (record.kind, record.width, record.height) == ("video", 32, 24)
    assert record.duration_seconds == pytest.approx(1, abs=0.05)
    assert record.media_type == mime


def test_video_cannot_follow_external_playlist(tmp_path):
    from pipe_inspector.media import MediaError, MediaStore

    with pytest.raises(MediaError) as caught:
        MediaStore(tmp_path).ingest(io.BytesIO(b"#EXTM3U\nhttp://127.0.0.1/private\n"))
    assert caught.value.status_code == 415


def test_missing_media_tool_is_not_reported_as_a_valid_upload(tmp_path, monkeypatch):
    from pipe_inspector.media import MediaError, MediaStore

    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(MediaError) as caught:
        MediaStore(tmp_path).ingest(io.BytesIO(b"possible video"))
    assert caught.value.status_code == 503


def test_resolver_rejects_symlink_escape(tmp_path):
    from pipe_inspector.contracts.assets import AssetRef
    from pipe_inspector.media import MediaError, MediaStore

    root = tmp_path / "assets"
    root.mkdir()
    ref = AssetRef(asset_id=uuid4())
    (root / str(ref.asset_id)).symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(MediaError):
        MediaStore(root).path_for(ref)
