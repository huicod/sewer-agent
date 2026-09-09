"""Original-media admission. Full video coverage is an inspection responsibility."""

import json
import math
import subprocess
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from PIL import Image, UnidentifiedImageError

from pipe_inspector.contracts.assets import AssetKind, AssetRecord, AssetRef


class MediaError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 415):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class MediaStore:
    def __init__(
        self,
        root: Path,
        *,
        max_upload_bytes: int = 1024**3,
        max_pixels: int = 40_000_000,
        probe_timeout_seconds: float = 20,
    ):
        self.root = root.resolve()
        self.max_upload_bytes = max_upload_bytes
        self.max_pixels = max_pixels
        self.probe_timeout_seconds = probe_timeout_seconds

    def path_for(self, ref: AssetRef) -> Path:
        path = self.root / str(ref.asset_id) / str(ref.version) / "source"
        if not path.resolve().is_relative_to(self.root):
            raise MediaError("asset_unavailable", "资产路径不可用", 404)
        return path

    def ingest(self, stream: BinaryIO) -> AssetRecord:
        ref = AssetRef(asset_id=uuid4())
        path = self.path_for(ref)
        path.parent.mkdir(parents=True, exist_ok=False)
        try:
            byte_size = 0
            with path.open("xb") as target:
                while chunk := stream.read(1024 * 1024):
                    byte_size += len(chunk)
                    if byte_size > self.max_upload_bytes:
                        raise MediaError("upload_too_large", "文件超过上传大小限制", 413)
                    target.write(chunk)
            if byte_size == 0:
                raise MediaError("invalid_media", "文件为空或不是支持的媒体")
            metadata = self._inspect(path)
            return AssetRecord(ref=ref, byte_size=byte_size, **metadata)
        except BaseException:
            # Only this request's newly created file/directories; no broad cleanup.
            path.unlink(missing_ok=True)
            path.parent.rmdir()
            path.parent.parent.rmdir()
            raise

    def _dimensions(self, width: int, height: int) -> None:
        if width <= 0 or height <= 0:
            raise MediaError("invalid_media", "媒体没有有效画面尺寸")
        if width * height > self.max_pixels:
            raise MediaError("image_too_large", "画面像素数超过限制", 413)

    def _inspect(self, path: Path) -> dict:
        try:
            with Image.open(path) as picture:
                if picture.format not in {"JPEG", "PNG"}:
                    raise MediaError("unsupported_media", "图片仅支持 JPEG 或 PNG")
                width, height = picture.size
                self._dimensions(width, height)
                mime = Image.MIME[picture.format]
                picture.verify()
            with Image.open(path) as picture:
                picture.load()
            return dict(kind=AssetKind.IMAGE, media_type=mime, width=width, height=height)
        except UnidentifiedImageError:
            # Unknown image signatures may be supported video containers.
            return self._inspect_video(path)
        except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
            raise MediaError("invalid_media", "图片损坏或无法解码") from exc

    def _run(self, args: list[str]) -> bytes:
        try:
            result = subprocess.run(
                args,
                capture_output=True,
                timeout=self.probe_timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise MediaError(
                "media_tool_unavailable", "服务器尚未安装 FFmpeg 媒体工具", 503
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise MediaError("media_probe_timeout", "媒体校验超时，请检查文件", 415) from exc
        if result.returncode != 0:
            raise MediaError("invalid_media", "媒体格式不支持、损坏或无法解码")
        return result.stdout

    def _inspect_video(self, path: Path) -> dict:
        restrictions = ["-protocol_whitelist", "file", "-format_whitelist", "mov,avi,matroska"]
        output = self._run(
            [
                "ffprobe",
                "-v",
                "error",
                *restrictions,
                "-show_entries",
                "stream=codec_type,width,height,duration:format=format_name,duration"
                ":format_tags=major_brand",
                "-of",
                "json",
                str(path),
            ]
        )
        try:
            info = json.loads(output)
            video = next(s for s in info["streams"] if s.get("codec_type") == "video")
            width, height = int(video["width"]), int(video["height"])
            duration = float(video.get("duration", info["format"].get("duration", 0)))
            container = info["format"]["format_name"].split(",")[0]
            mime = {"mov": "video/mp4", "avi": "video/x-msvideo", "matroska": "video/x-matroska"}[
                container
            ]
            if (
                container == "mov"
                and info["format"].get("tags", {}).get("major_brand", "").strip() == "qt"
            ):
                mime = "video/quicktime"
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError("invalid duration")
        except (ValueError, KeyError, TypeError, StopIteration) as exc:
            raise MediaError("invalid_media", "视频没有有效画面或时长") from exc
        self._dimensions(width, height)
        frame = self._run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-nostdin",
                "-xerror",
                *restrictions,
                "-threads",
                "1",
                "-i",
                str(path),
                "-map",
                "0:v:0",
                "-frames:v",
                "1",
                "-vf",
                "scale=1:1",
                "-threads",
                "1",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "gray",
                "pipe:1",
            ]
        )
        if len(frame) != 1:
            raise MediaError("invalid_media", "视频首帧无法解码")
        return dict(
            kind=AssetKind.VIDEO,
            media_type=mime,
            width=width,
            height=height,
            duration_seconds=duration,
        )
