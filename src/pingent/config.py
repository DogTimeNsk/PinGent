"""Local configuration for a self-hosted Pingent installation."""

from ipaddress import ip_address
from pathlib import Path

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ExternalAdapterSettings(BaseModel):
    """One locally supervised, out-of-process channel adapter."""

    command: list[str]
    actor_id: int

    @field_validator("command")
    @classmethod
    def validate_command(cls, value: list[str]) -> list[str]:
        if not value or any(not item for item in value):
            raise ValueError("External adapter command must contain non-empty arguments")
        return value

    @field_validator("actor_id")
    @classmethod
    def validate_actor_id(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("External adapter actor_id must be positive")
        return value


class Settings(BaseSettings):
    """Settings loaded only from the local environment and `.env` file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="PINGENT_",
        extra="ignore",
    )

    telegram_bot_token: SecretStr
    allowed_telegram_user_ids: set[int]
    workspaces: list[Path]
    database_path: Path = Path("data/pingent.sqlite3")
    codex_binary: str = "codex"
    codex_model: str | None = None
    codex_request_timeout_seconds: float = Field(default=30, gt=0)
    locale: str = "en"
    web_enabled: bool = False
    web_host: str = "127.0.0.1"
    web_port: int = 8080
    web_allow_remote: bool = False
    web_secure_cookies: bool = False
    external_adapters: list[ExternalAdapterSettings] = []

    @field_validator("locale")
    @classmethod
    def validate_locale(cls, value: str) -> str:
        if value not in {"en", "ru"}:
            raise ValueError("PINGENT_LOCALE must be one of: en, ru")
        return value

    @field_validator("allowed_telegram_user_ids")
    @classmethod
    def validate_allowed_users(cls, value: set[int]) -> set[int]:
        if not value:
            raise ValueError("PINGENT_ALLOWED_TELEGRAM_USER_IDS must not be empty")
        return value

    @field_validator("workspaces")
    @classmethod
    def validate_workspaces(cls, value: list[Path]) -> list[Path]:
        if not value:
            raise ValueError("PINGENT_WORKSPACES must contain at least one directory")

        resolved_workspaces: list[Path] = []
        for workspace in value:
            resolved = workspace.expanduser().resolve(strict=True)
            if not resolved.is_dir():
                raise ValueError(f"Workspace is not a directory: {resolved}")
            resolved_workspaces.append(resolved)
        return resolved_workspaces

    @field_validator("web_host")
    @classmethod
    def validate_web_host(cls, value: str) -> str:
        ip_address(value)
        return value

    @model_validator(mode="after")
    def validate_web_bind(self) -> "Settings":
        if not ip_address(self.web_host).is_loopback and not self.web_allow_remote:
            raise ValueError("PINGENT_WEB_ALLOW_REMOTE=true is required for a non-loopback host")
        if not ip_address(self.web_host).is_loopback and not self.web_secure_cookies:
            raise ValueError("PINGENT_WEB_SECURE_COOKIES=true is required for a non-loopback host")
        return self

    @model_validator(mode="after")
    def validate_external_adapters(self) -> "Settings":
        actor_ids = [adapter.actor_id for adapter in self.external_adapters]
        if len(actor_ids) != len(set(actor_ids)):
            raise ValueError("PINGENT_EXTERNAL_ADAPTERS actor_id values must be unique")
        if set(actor_ids) & self.allowed_telegram_user_ids:
            raise ValueError("External adapter actor_id must not be a Telegram user ID")
        return self
