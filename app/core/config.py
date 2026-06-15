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

    # Email
    # Active transport: "smtp" (Gmail today) or "resend" (future — requires a
    # DNS-verified sending domain). The business code depends on the EmailSender
    # port, so switching is a one-variable change.
    email_provider: str = "smtp"
    # Public base URL of the frontend, used to build links inside emails (e.g. the
    # account-verification link points at {frontend_base_url}/api/auth/verify). No trailing slash.
    frontend_base_url: str = "http://localhost:3000"
    # Sender identity shown to recipients.
    email_sender_name: str = "Integrity Solutions"
    email_sender_email: str = ""
    # SMTP transport (Gmail by default). `smtp_password` is a Gmail app-password.
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    # Resend transport (future). Set EMAIL_PROVIDER=resend once the domain is verified.
    resend_api_key: str = ""

    # Meetings (Teams via Microsoft Graph)
    # "disabled" (default, safe) or "graph". When "graph", the three azure_* values
    # below are required (Azure AD app with OnlineMeetings.ReadWrite.All + a Teams
    # application access policy authorizing the organizer).
    meetings_provider: str = "disabled"
    azure_tenant_id: str = ""
    azure_client_id: str = ""
    azure_client_secret: str = ""

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
