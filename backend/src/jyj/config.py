from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_DATABASE_URL = "postgresql+psycopg://jyj:jyj@127.0.0.1:5432/jyj"
PLACEHOLDER = "change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["development", "test", "production"] = "development"
    database_url: str = Field(default=DEV_DATABASE_URL, repr=False)
    db_echo: bool = False
    session_secret: SecretStr = SecretStr("dev-insecure-session-secret")
    # Only turn off for plain-http local dev; browsers drop Secure cookies over http.
    session_cookie_secure: bool = True
    codex_enabled: bool = True
    codex_binary: str = "codex"
    codex_model: str | None = None
    codex_timeout_seconds: float = Field(default=90.0, gt=0)

    whisper_binary: str = "whisper-cli"
    whisper_model_path: Path = Path("/var/lib/jyj/whisper/ggml-tiny.bin")
    whisper_threads: int = Field(default=4, ge=1, le=16)
    whisper_timeout_seconds: float = Field(default=30.0, gt=0)
    stt_max_bytes: int = Field(default=5 * 1024 * 1024, gt=0)
    stt_max_seconds: float = Field(default=60.0, gt=0)

    photos_dir: Path = Path("/var/lib/jyj/photos")
    photo_max_bytes: int = Field(default=10 * 1024 * 1024, gt=0)

    @model_validator(mode="after")
    def _require_explicit_values_in_production(self) -> "Settings":
        if self.app_env != "production":
            return self
        missing = [
            name for name in ("database_url", "session_secret") if name not in self.model_fields_set
        ]
        if missing:
            names = ", ".join(n.upper() for n in missing)
            raise ValueError(f"{names} must be set when APP_ENV=production")
        if PLACEHOLDER in self.session_secret.get_secret_value():
            raise ValueError("SESSION_SECRET still holds the placeholder value")
        if len(self.session_secret.get_secret_value()) < 32:
            raise ValueError("SESSION_SECRET must be at least 32 characters in production")
        if not self.session_cookie_secure:
            raise ValueError("SESSION_COOKIE_SECURE cannot be false in production")
        return self

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
