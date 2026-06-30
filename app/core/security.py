import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt

from app.core.config import settings

# bcrypt operates on the first 72 bytes of the password (algorithm limit).
# API schemas cap password length, so this is a safety net, not silent truncation.
_BCRYPT_MAX_BYTES = 72


def hash_password(plain_password: str) -> str:
    pw = plain_password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    return bcrypt.hashpw(pw, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    pw = plain_password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    return bcrypt.checkpw(pw, password_hash.encode("utf-8"))


def _create_token(
    subject: str | int,
    expires_delta: timedelta,
    token_type: str,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "type": token_type,
        "iat": now,
        "exp": now + expires_delta,
        # Unique nonce so two tokens for the same subject minted in the same
        # second are never byte-identical — otherwise their hash collides on
        # uq_refresh_tokens_token_hash and refresh rotation fails.
        "jti": secrets.token_urlsafe(16),
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(
    subject: str | int,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    return _create_token(
        subject,
        timedelta(minutes=settings.access_token_expire_minutes),
        "access",
        extra_claims,
    )


def create_verification_token(subject: str | int) -> str:
    return _create_token(
        subject,
        timedelta(hours=24),
        "verification",
    )


def password_fingerprint(password_hash: str) -> str:
    """Short, stable fingerprint of a password hash.

    Embedded in password-reset tokens to make them single-use without any
    server-side storage: resetting the password re-hashes it (bcrypt uses a
    random salt, so even the same password yields a new hash), which changes the
    fingerprint and invalidates every token issued against the previous hash.
    """
    import hashlib

    return hashlib.sha256(password_hash.encode("utf-8")).hexdigest()[:16]


def create_password_reset_token(subject: str | int, password_hash: str) -> str:
    """Stateless, single-use reset token (1h). Bound to the current password via
    a fingerprint claim so it cannot be replayed after the password changes."""
    return _create_token(
        subject,
        timedelta(hours=1),
        "password_reset",
        {"pwf": password_fingerprint(password_hash)},
    )


def hash_token(token: str) -> str:
    """Deterministic hash for refresh-token persistence (lookup by hash, never store raw)."""
    import hashlib

    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_refresh_token(subject: str | int) -> str:
    return _create_token(
        subject,
        timedelta(days=settings.refresh_token_expire_days),
        "refresh",
    )


def decode_token(token: str) -> dict[str, Any]:
    """Decode and validate a JWT. Raises jwt.PyJWTError on invalid/expired token."""
    return jwt.decode(
        token,
        settings.jwt_secret_key,
        algorithms=[settings.jwt_algorithm],
    )
