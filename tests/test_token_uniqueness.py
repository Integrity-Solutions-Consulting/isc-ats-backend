"""Tokens minted for the same subject at the same instant must still be unique.

Without a unique nonce the JWT payload (sub/type/iat/exp at second precision) is
byte-identical for two tokens issued in the same second, so their hash collides
on `uq_refresh_tokens_token_hash` and refresh-token rotation 500s.
"""

from datetime import UTC, datetime
from unittest.mock import patch

import jwt

from app.core.config import settings
from app.core.security import create_access_token, create_refresh_token


def _decode(token: str) -> dict:
    return jwt.decode(
        token,
        settings.jwt_secret_key,
        algorithms=[settings.jwt_algorithm],
        options={"verify_exp": False},
    )


def test_access_tokens_same_instant_are_unique() -> None:
    fixed = datetime(2026, 6, 30, 12, 0, 0, tzinfo=UTC)
    with patch("app.core.security.datetime") as mock_dt:
        mock_dt.now.return_value = fixed
        t1 = create_access_token(1)
        t2 = create_access_token(1)

    assert t1 != t2
    assert _decode(t1)["jti"] != _decode(t2)["jti"]


def test_refresh_tokens_same_instant_are_unique() -> None:
    fixed = datetime(2026, 6, 30, 12, 0, 0, tzinfo=UTC)
    with patch("app.core.security.datetime") as mock_dt:
        mock_dt.now.return_value = fixed
        t1 = create_refresh_token(1)
        t2 = create_refresh_token(1)

    assert t1 != t2
    assert _decode(t1)["jti"] != _decode(t2)["jti"]
