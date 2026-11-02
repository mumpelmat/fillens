from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", hide_input_in_errors=True
    )
    database_url: str
    encryption_key: str
    data_dir: Path = Path("/data")
    ml_data_dir: Path = Path("/ml")
    app_base_path: str = ""
    cookie_secure: bool = True
    session_hours: int = Field(default=12, ge=1, le=168)
    ftp_poll_seconds: int = Field(default=60, ge=1, le=86400)
    ftp_settle_seconds: int = Field(default=30, ge=0, le=86400)
    ftp_timeout_seconds: int = Field(default=20, ge=1, le=120)
    max_image_bytes: int = Field(
        default=15 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024
    )
    max_scan_files: int = Field(default=10000, ge=1, le=1000000)
    max_imports_per_cycle: int = Field(default=100, ge=1, le=10000)

    @field_validator("app_base_path")
    @classmethod
    def base_path(cls, value: str) -> str:
        if value and (
            not value.startswith("/") or value.endswith("/") or ".." in value
        ):
            raise ValueError("APP_BASE_PATH muss leer oder z. B. /fuellstand sein")
        return value

    @field_validator("encryption_key")
    @classmethod
    def key(cls, value: str) -> str:
        Fernet(value.encode())
        return value


@lru_cache
def settings() -> Settings:
    return Settings()
