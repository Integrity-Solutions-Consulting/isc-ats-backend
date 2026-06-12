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


def test_production_with_real_jwt_secret_constructs_fine() -> None:
    """A strong secret must allow the Settings to construct in production."""
    s = _make(
        environment="production",
        jwt_secret_key="super-long-random-secret-safe-for-prod-1234567890abcdef",
        debug=False,
    )
    assert s.is_production is True


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
