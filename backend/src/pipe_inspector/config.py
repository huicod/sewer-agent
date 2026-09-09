"""Server-owned configuration; secrets must never enter result payloads."""

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SEWER_", env_file=".env", extra="ignore")

    asset_root: Path = Path("var/assets")
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
    api_timeout_seconds: float = 60.0

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_base_url and self.llm_model and self.llm_api_key)

    @property
    def vlm_configured(self) -> bool:
        return bool(self.vlm_base_url and self.vlm_model and self.vlm_api_key)
