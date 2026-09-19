"""Environment configuration; secrets have no development fallback."""

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str = Field(min_length=1)
    secret_key: str = Field(min_length=32, repr=False)
    access_token_expire_minutes: int = Field(default=30, ge=1, le=1440)
    public_base_url: str = "http://localhost:8000"
    upload_dir: Path = Path("uploads")
    max_upload_bytes: int = Field(default=5 * 1024 * 1024, ge=1024, le=25 * 1024 * 1024)
    cors_origins: list[str] = Field(default_factory=list)

    @field_validator("secret_key")
    @classmethod
    def validate_secret(cls, value: str) -> str:
        if len(value.strip()) < 32:
            raise ValueError("SECRET_KEY deve conter ao menos 32 caracteres não vazios.")
        return value

    @field_validator("public_base_url")
    @classmethod
    def validate_public_base_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("PUBLIC_BASE_URL deve ser uma URL HTTP ou HTTPS absoluta.")
        if parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise ValueError("PUBLIC_BASE_URL não pode conter credenciais, query ou fragmento.")
        return value.rstrip("/")


@lru_cache
def get_settings() -> Settings:
    return Settings()
