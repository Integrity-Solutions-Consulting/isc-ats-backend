"""Unit tests for Settings security constraints.

These tests construct Settings instances directly with explicit kwargs so they
are isolated from the real .env file and any ambient environment variables.
No database connection is required.
"""

import pytest

from app.core.config import Settings


def _make(**kwargs) -> Settings:
    """Build a Settings instance without reading .env or env vars.

    Passing ``_env_file=None`` via the pydantic-settings init API disables .env
    loading so tests are fully isolated from the developer's local .env file.
    """
    return Settings(_env_file=None, **kwargs)


# ---------------------------------------------------------------------------
# 1. Production guard — JWT secret must not be the well-known default
# ---------------------------------------------------------------------------


def test_production_with_default_jwt_secret_raises() -> None:
    """Boot must fail when environment=production and jwt_secret_key is the default."""
    with pytest.raises(ValueError, match="jwt_secret_key"):
        _make(
            environment="production",
            jwt_secret_key="change-me-in-production",
            debug=False,
        )


_PROD_JWT = "super-long-random-secret-safe-for-prod-1234567890abcdef"


def test_production_with_real_jwt_secret_constructs_fine() -> None:
    """A strong secret + non-default MinIO creds must construct in production."""
    s = _make(
        environment="production",
        jwt_secret_key=_PROD_JWT,
        minio_access_key="real-access-key",
        minio_secret_key="real-secret-key",
        debug=False,
    )
    assert s.is_production is True


# ---------------------------------------------------------------------------
# 1b. Production guard — MinIO credentials must not be the well-known default
# ---------------------------------------------------------------------------


def test_production_with_default_minio_credentials_raises() -> None:
    """Boot must fail when environment=production and MinIO creds are 'minioadmin'."""
    with pytest.raises(ValueError, match="minio"):
        _make(
            environment="production",
            jwt_secret_key=_PROD_JWT,
            minio_access_key="minioadmin",
            minio_secret_key="minioadmin",
            debug=False,
        )


def test_development_with_default_minio_credentials_is_allowed() -> None:
    """Defaults are fine outside production (local docker-compose)."""
    s = _make(environment="development")
    assert s.minio_access_key == "minioadmin"


# ---------------------------------------------------------------------------
# 2 & 3. Safe defaults for debug, sql_echo, and minio_secure
# ---------------------------------------------------------------------------


def test_default_flags_are_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """debug, sql_echo, and minio_secure must all default to False.

    ``_env_file=None`` only disables .env loading; real environment variables
    (e.g. DEBUG=true in the dev container) still reach pydantic-settings, so
    they must be removed for the defaults to be observable.
    """
    for var in ("DEBUG", "SQL_ECHO", "MINIO_SECURE"):
        monkeypatch.delenv(var, raising=False)
    s = _make()
    assert s.debug is False
    assert s.sql_echo is False
    assert s.minio_secure is False
