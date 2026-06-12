from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_DEFAULT_JWT_SECRET = "change-me-in-production"


class Settings(BaseSettings):
    """Application settings loaded from environment variables / .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    app_name: str = "ISC ATS Backend"
    environment: str = "development"
    # Safe default: debug mode is opt-in, not opt-out.
    debug: bool = False
    api_v1_prefix: str = "/api/v1"

    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/isc_ats"
    # Separate SQL echo from the FastAPI debug flag so they can be toggled independently.
    sql_echo: bool = False

    # MinIO / S3 Storage
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "candidates-cvs"
    # TLS for MinIO traffic — False keeps local docker-compose working; enable in production.
    minio_secure: bool = False

    # Security
    jwt_secret_key: str = _DEFAULT_JWT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    # AI / LLM
    gemini_api_key: str = ""
    anthropic_api_key: str = ""

    # CORS — NoDecode so a comma-separated env string isn't JSON-parsed by the source
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, value: object) -> object:
        """Accept a comma-separated string from the env, not just JSON arrays."""
        if isinstance(value, str) and not value.startswith("["):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @model_validator(mode="after")
    def _reject_default_jwt_secret_in_production(self) -> "Settings":
        """Prevent the application from booting in production with the well-known default JWT secret.

        Anyone who knows the default value can forge valid tokens, so we fail loudly
        rather than silently accepting an insecure configuration.
        """
        if self.is_production and self.jwt_secret_key == _DEFAULT_JWT_SECRET:
            raise ValueError(
                "jwt_secret_key must be changed from the default value before running in production. "
                "Set the JWT_SECRET_KEY environment variable to a strong random secret."
            )
        return self

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()


settings = get_settings()
