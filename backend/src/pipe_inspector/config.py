"""Server-owned configuration; secrets must never enter result payloads."""

from pathlib import Path

import httpx
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SEWER_", env_file=".env", extra="ignore", populate_by_name=True
    )

    asset_root: Path = Path("var/assets")
    max_upload_bytes: int = Field(default=1024**3, gt=0)
    max_media_pixels: int = Field(default=40_000_000, gt=0)
    media_probe_timeout_seconds: float = Field(default=20, gt=0, allow_inf_nan=False)
    database_dsn: SecretStr = SecretStr("postgresql://sewer_agent@127.0.0.1:5432/sewer_agent")
    temporal_address: str = "127.0.0.1:7233"
    temporal_namespace: str = "default"
    task_queue: str = "sewer-inspection"
    detector_path: Path = Path(
        "yolov26/experiments/sewer2403_5class_yolo26s_img960_e100/weights/best.pt"
    )
    detector_device: str = "0"
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: SecretStr | None = None
    vlm_base_url: str | None = None
    vlm_model: str | None = None
    vlm_api_key: SecretStr | None = None
    dashscope_api_key: SecretStr | None = Field(
        default=None, validation_alias="DASHSCOPE_API_KEY", repr=False, exclude=True
    )
    api_enable_thinking: bool | None = None
    api_max_output_tokens: int | None = Field(default=None, ge=1, le=8192)
    api_timeout_seconds: float = Field(default=60, gt=0, allow_inf_nan=False)
    vlm_max_image_bytes: int = Field(default=8 * 1024**2, gt=0)

    @model_validator(mode="after")
    def resolve_dashscope_key(self):
        """Reuse the shared key only for explicit Qwen roles on official HTTPS hosts."""
        if self.dashscope_api_key is None:
            return self
        for role in ("llm", "vlm"):
            if getattr(self, f"{role}_api_key") is not None:
                continue
            base_url = getattr(self, f"{role}_base_url")
            model = getattr(self, f"{role}_model")
            if not base_url or not model or not model.startswith("qwen"):
                continue
            try:
                url = httpx.URL(base_url)
            except (TypeError, ValueError):
                continue
            official = url.host in {
                "dashscope.aliyuncs.com",
                "dashscope-intl.aliyuncs.com",
                "dashscope-us.aliyuncs.com",
                "cn-hongkong.dashscope.aliyuncs.com",
            } or url.host.endswith(".maas.aliyuncs.com")
            if (
                official
                and url.scheme == "https"
                and not url.username
                and not url.password
                and not url.query
                and not url.fragment
            ):
                setattr(self, f"{role}_api_key", self.dashscope_api_key)
        return self

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_base_url and self.llm_model and self.llm_api_key)

    @property
    def vlm_configured(self) -> bool:
        return bool(self.vlm_base_url and self.vlm_model and self.vlm_api_key)
