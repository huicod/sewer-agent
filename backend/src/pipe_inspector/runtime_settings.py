"""Versioned, server-only API credentials shared by admission and worker processes."""

import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Literal
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from pipe_inspector.config import Settings

API_FIELDS = (
    "llm_base_url",
    "llm_model",
    "llm_api_key",
    "vlm_base_url",
    "vlm_model",
    "vlm_api_key",
    "api_timeout_seconds",
    "api_max_output_tokens",
    "api_enable_thinking",
)
REVISION_PIN = "api_settings_revision"


class RuntimeSettingsError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 409):
        self.code, self.status_code = code, status_code
        super().__init__(message)


def checked_base_url(value: str) -> str:
    value = value.strip().rstrip("/")
    if not value:
        return ""
    try:
        url = httpx.URL(value)
    except (TypeError, ValueError):
        raise ValueError("API 地址无效") from None
    if (
        url.scheme not in {"http", "https"}
        or not url.host
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise ValueError("API 地址无效；凭据只能填写在 API Key 中")
    return str(url).rstrip("/")


class RoleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    base_url: str = Field(max_length=2048)
    model: str = Field(max_length=200)
    api_key: SecretStr | None = None
    clear_api_key: bool = False

    _base_url = field_validator("base_url")(checked_base_url)

    @field_validator("model")
    @classmethod
    def trim_model(cls, value):
        return value.strip()

    @field_validator("api_key")
    @classmethod
    def validate_key(cls, value):
        if value is not None:
            key = value.get_secret_value().strip()
            if len(key) > 8192 or any(c in key for c in "\r\n\x00"):
                raise ValueError("API Key 格式无效")
            return SecretStr(key) if key else None
        return value

    @model_validator(mode="after")
    def key_action(self):
        if self.clear_api_key and self.api_key is not None:
            raise ValueError("不能同时填写和清除 API Key")
        if bool(self.base_url) != bool(self.model):
            raise ValueError("请同时填写 API 地址和模型名称")
        return self


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    revision: int = Field(ge=0, strict=True)
    llm: RoleUpdate
    vlm: "VisionRoleUpdate"
    api_timeout_seconds: float = Field(ge=1, le=600, allow_inf_nan=False)
    api_max_output_tokens: int | None = Field(default=None, ge=1, le=8192, strict=True)
    api_enable_thinking: bool | None = None


class ConnectionTest(RoleUpdate):
    role: Literal["llm", "vlm"]


class VisionRoleUpdate(RoleUpdate):
    reuse_llm_key: bool = False


SettingsUpdate.model_rebuild()


def _cloud_values(settings):
    return {
        name: value.get_secret_value() if isinstance(value, SecretStr) else value
        for name in API_FIELDS
        for value in (getattr(settings, name),)
    }


@contextmanager
def _locked(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if os.name == "nt":
            import msvcrt

            if os.fstat(fd).st_size == 0:
                os.write(fd, b"0")
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


class RuntimeSettingsStore:
    def __init__(self, baseline: Settings):
        self.baseline = baseline
        self.path = baseline.runtime_settings_dir / "api-settings.json"

    def _read(self, *, require_existing=False):
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            if require_existing:
                raise RuntimeSettingsError(
                    "settings_revision_missing", "任务固定的 API 设置版本不可用", 503
                ) from None
            return {
                "schema_version": 1,
                "revision": 0,
                "versions": {"0": _cloud_values(self.baseline)},
            }
        except (OSError, ValueError):
            raise RuntimeSettingsError("settings_unavailable", "API 设置暂不可用", 503) from None
        if (
            not isinstance(raw, dict)
            or raw.get("schema_version") != 1
            or type(raw.get("revision")) is not int
            or raw["revision"] < 0
            or not isinstance(raw.get("versions"), dict)
        ):
            raise RuntimeSettingsError("settings_unavailable", "API 设置格式无效", 503)
        return raw

    def resolve(self, revision: int | None = None) -> Settings:
        raw = self._read(require_existing=revision is not None)
        selected = raw["revision"] if revision is None else revision
        if type(selected) is not int or selected < 0 or str(selected) not in raw["versions"]:
            raise RuntimeSettingsError(
                "settings_revision_missing", "任务固定的 API 设置版本不可用", 503
            )
        values = raw["versions"][str(selected)]
        if not isinstance(values, dict) or set(values) != set(API_FIELDS):
            raise RuntimeSettingsError("settings_unavailable", "API 设置格式无效", 503)
        try:
            result = Settings.model_validate(
                self.baseline.model_dump() | values | {"dashscope_api_key": None}
            )
        except ValueError:
            raise RuntimeSettingsError("settings_unavailable", "API 设置格式无效", 503) from None
        result._api_settings_revision = selected
        result._api_settings_frozen = True
        return result

    def admit(self) -> Settings:
        # The first admitted revision must survive a restart/environment key rotation,
        # even if nobody has opened or saved the settings page yet.
        if self.path.is_file():
            return self.resolve()
        with _locked(self.path.with_suffix(".lock")):
            raw = self._read()
            if not self.path.is_file():
                self._write(raw)
            return self.resolve(raw["revision"])

    def _write(self, raw):
        temporary = self.path.with_name(f".api-settings-{uuid4().hex}.tmp")
        try:
            fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(raw, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def public(self):
        settings = self.resolve()
        roles = {}
        for role in ("llm", "vlm"):
            try:
                endpoint = checked_base_url(getattr(settings, f"{role}_base_url") or "")
            except ValueError:
                # A legacy environment URL may contain credentials. Never echo it to the browser.
                endpoint = ""
            roles[role] = {
                "base_url": endpoint,
                "model": getattr(settings, f"{role}_model") or "",
                "key_configured": bool(getattr(settings, f"{role}_api_key")),
                "configured": bool(endpoint and getattr(settings, f"{role}_configured")),
            }
        return {
            "revision": settings._api_settings_revision,
            **roles,
            "api_timeout_seconds": settings.api_timeout_seconds,
            "api_max_output_tokens": settings.api_max_output_tokens,
            "api_enable_thinking": settings.api_enable_thinking,
        }

    @staticmethod
    def role_key(current: Settings, role: str, update: RoleUpdate):
        if update.clear_api_key:
            return None
        if update.api_key is not None:
            return update.api_key
        previous = getattr(current, f"{role}_api_key")
        try:
            previous_endpoint = checked_base_url(getattr(current, f"{role}_base_url") or "")
        except ValueError:
            previous_endpoint = None
        if previous and previous_endpoint != update.base_url:
            raise RuntimeSettingsError(
                "settings_key_required", "更换 API 地址后请重新填写 API Key", 422
            )
        return previous

    def save(self, update: SettingsUpdate):
        with _locked(self.path.with_suffix(".lock")):
            raw = self._read()
            if update.revision != raw["revision"]:
                raise RuntimeSettingsError(
                    "settings_conflict", "设置已在其他页面更新，请重新加载后保存"
                )
            current = self.resolve()
            values = _cloud_values(current)
            for role in ("llm", "vlm"):
                item = getattr(update, role)
                if role == "vlm" and item.reuse_llm_key:
                    if (
                        item.base_url != update.llm.base_url
                        or item.model != update.llm.model
                        or item.clear_api_key
                        or item.api_key is not None
                    ):
                        raise RuntimeSettingsError(
                            "settings_invalid", "共享密钥时请使用相同的 API 地址和模型", 422
                        )
                    key = SecretStr(values["llm_api_key"]) if values["llm_api_key"] else None
                else:
                    key = self.role_key(current, role, item)
                values.update(
                    {
                        f"{role}_base_url": item.base_url or None,
                        f"{role}_model": item.model or None,
                        f"{role}_api_key": key.get_secret_value() if key else None,
                    }
                )
            for name in ("api_timeout_seconds", "api_max_output_tokens", "api_enable_thinking"):
                values[name] = getattr(update, name)
            revision = raw["revision"] + 1
            raw["versions"][str(revision)] = values
            raw["revision"] = revision
            self._write(raw)
            return self.public()


def current_api_settings(settings: Settings) -> Settings:
    # A starter created for a pinned conversation must retain that exact revision.
    if settings._api_settings_frozen:
        return settings
    return RuntimeSettingsStore(settings).admit()


def settings_for_run(settings: Settings, config: dict) -> Settings:
    if REVISION_PIN not in config:
        legacy = settings.model_copy()
        legacy._api_settings_frozen = True
        return legacy
    return RuntimeSettingsStore(settings).resolve(config[REVISION_PIN])


def revision_pin(settings: Settings) -> dict:
    revision = settings._api_settings_revision
    return {REVISION_PIN: revision} if revision is not None else {}
