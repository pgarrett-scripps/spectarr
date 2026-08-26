from __future__ import annotations

from functools import lru_cache
from ipaddress import ip_address
from pathlib import Path
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SPECTARR_",
        env_file=".env",
        extra="ignore",
    )

    app_name: str = "Spectarr"
    environment: str = "development"
    api_prefix: str = "/api/v1"
    database_url: str = "sqlite:///./data/spectarr.db"
    storage_root: Path = Path("./data/storage")
    library_root: Path | None = None
    library_link_mode: str = "auto"
    library_project_template: str = "{project_name}__{project_id:8}"
    library_filename_template: str = "{run_name}__{sample_name}__{run_id:8}{extension}"
    import_roots: list[Path] = []
    max_upload_bytes: int = 100 * 1024 * 1024 * 1024
    worker_token: str | None = None
    job_lease_seconds: int = 300
    auth_mode: Literal["password", "local"] = "password"
    local_user: str = "admin"
    allow_remote_no_auth: bool = False
    bind_address: str = "127.0.0.1"
    auth_enabled: bool | None = None
    session_hours: int = 24
    upload_session_hours: int = 24
    secret_key: str = "development-only-change-me"
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:5173"]
    login_max_attempts: int = 5
    login_window_seconds: int = 300
    login_lock_seconds: int = 900
    spectrum_reader_url: str | None = None
    spectrum_reader_timeout_seconds: float = 30.0

    @field_validator("storage_root", mode="before")
    @classmethod
    def expand_storage_root(cls, value: str | Path) -> Path:
        return Path(value).expanduser()

    @field_validator("library_root", mode="before")
    @classmethod
    def expand_library_root(cls, value: str | Path | None) -> Path | None:
        return Path(value).expanduser() if value else None

    @field_validator("library_link_mode")
    @classmethod
    def validate_library_link_mode(cls, value: str) -> str:
        if value not in {"auto", "hardlink", "copy"}:
            raise ValueError("library_link_mode must be auto, hardlink, or copy")
        return value

    @field_validator("library_project_template")
    @classmethod
    def validate_project_template(cls, value: str) -> str:
        if "{project_id" not in value:
            raise ValueError("library_project_template must include project_id")
        return value

    @field_validator("library_filename_template")
    @classmethod
    def validate_filename_template(cls, value: str) -> str:
        if "{extension}" not in value:
            raise ValueError("library_filename_template must include extension")
        return value

    @field_validator("import_roots", mode="before")
    @classmethod
    def parse_import_roots(cls, value: object) -> object:
        if isinstance(value, str):
            return [
                Path(item).expanduser() for item in value.split(",") if item.strip()
            ]
        return value

    @field_validator("spectrum_reader_url", mode="before")
    @classmethod
    def normalize_spectrum_reader_url(cls, value: object) -> object:
        if value is None:
            return None
        normalized = str(value).strip()
        if normalized and not normalized.startswith(("http://", "https://")):
            raise ValueError("spectrum_reader_url must use http or https")
        return normalized or None

    @field_validator("spectrum_reader_timeout_seconds")
    @classmethod
    def validate_spectrum_reader_timeout(cls, value: float) -> float:
        if value <= 0 or value > 300:
            raise ValueError(
                "spectrum_reader_timeout_seconds must be greater than 0 and at most 300"
            )
        return value

    @field_validator("local_user")
    @classmethod
    def validate_local_user(cls, value: str) -> str:
        normalized = value.strip().lower()
        allowed = all(
            character.isalnum() or character in "._-" for character in normalized
        )
        if len(normalized) < 3 or not allowed:
            raise ValueError(
                "local_user must be at least three characters and use letters, numbers, dots, dashes, or underscores"
            )
        return normalized

    @property
    def effective_auth_mode(self) -> Literal["password", "local"]:
        # Preserve the original SPECTARR_AUTH_ENABLED=false deployment setting.
        return "local" if self.auth_enabled is False else self.auth_mode

    @model_validator(mode="after")
    def validate_local_auth_exposure(self) -> Settings:
        if self.effective_auth_mode != "local" or self.allow_remote_no_auth:
            return self
        try:
            is_local = ip_address(self.bind_address).is_loopback
        except ValueError:
            is_local = self.bind_address.strip().lower() == "localhost"
        if not is_local:
            raise ValueError(
                "local authentication mode requires a loopback bind_address or allow_remote_no_auth=true"
            )
        return self

    @model_validator(mode="after")
    def validate_production_security(self) -> Settings:
        if self.environment.casefold() != "production":
            return self
        weak_markers = {
            "development-only-change-me",
            "spectarr-local-development-secret-change-me",
        }
        if len(self.secret_key) < 32 or self.secret_key in weak_markers:
            raise ValueError(
                "production requires a unique SPECTARR_SECRET_KEY of at least 32 characters"
            )
        if (
            not self.worker_token
            or len(self.worker_token) < 32
            or self.worker_token == "spectarr-local-worker"
        ):
            raise ValueError(
                "production requires a unique SPECTARR_WORKER_TOKEN of at least 32 characters"
            )
        if self.database_url.startswith("sqlite"):
            raise ValueError("production requires PostgreSQL")
        if "spectarr:spectarr@" in self.database_url:
            raise ValueError("production cannot use the default PostgreSQL password")
        if "*" in self.cors_origins:
            raise ValueError("production CORS origins cannot contain a wildcard")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
