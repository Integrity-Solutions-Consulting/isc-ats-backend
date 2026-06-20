"""User-level access-token denylist (security fix 3.4).

Access tokens are short-lived JWTs validated statelessly — so until now they
could not be revoked before expiry. When a user changes their password or
deactivates their account, every access token issued before that moment must
stop working immediately (the credential may be compromised). We record a
per-user "revoked before" cutoff (a unix timestamp); ``get_current_user``
rejects any access token whose ``iat`` is at or before the cutoff.

Why user-level (not per-jti): change-password and self-deactivation already
revoke ALL of the user's refresh tokens, so revoking all of their access tokens
in one marker is the consistent, cheaper move — no ``jti`` claim needed, just
the ``iat`` already present in every JWT.

Two adapters mirror the task-queue split: in-memory (dev/test, single process)
and Redis (production, shared across API replicas + worker). The marker's TTL
equals the access-token lifetime — once no live token predates the cutoff the
marker is useless and expires on its own.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from redis.asyncio import Redis


class TokenDenylist(Protocol):
    """Port: revoke a user's access tokens and test a token against the cutoff."""

    async def revoke_user(self, user_id: int, ttl_seconds: int) -> None: ...

    async def is_user_revoked(self, user_id: int, issued_at: int) -> bool: ...


class InMemoryTokenDenylist:
    """Process-local denylist. Fine for tests and single-process dev."""

    def __init__(self) -> None:
        # user_id -> (cutoff_epoch, expires_at_monotonic)
        self._cutoffs: dict[int, tuple[int, float]] = {}

    async def revoke_user(self, user_id: int, ttl_seconds: int) -> None:
        self._cutoffs[user_id] = (int(time.time()), time.monotonic() + ttl_seconds)

    async def is_user_revoked(self, user_id: int, issued_at: int) -> bool:
        entry = self._cutoffs.get(user_id)
        if entry is None:
            return False
        cutoff, expires_at = entry
        if time.monotonic() >= expires_at:
            del self._cutoffs[user_id]  # lazy cleanup of an expired marker
            return False
        # Inclusive: a token minted in the very second of the revocation is killed.
        return issued_at <= cutoff


class RedisTokenDenylist:
    """Distributed denylist — one auto-expiring key per revoked user."""

    _PREFIX = "denylist:user:"

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def revoke_user(self, user_id: int, ttl_seconds: int) -> None:
        await self._redis.set(f"{self._PREFIX}{user_id}", int(time.time()), ex=ttl_seconds)

    async def is_user_revoked(self, user_id: int, issued_at: int) -> bool:
        raw = await self._redis.get(f"{self._PREFIX}{user_id}")
        if raw is None:
            return False
        return issued_at <= int(raw)


def build_token_denylist(redis: Redis | None = None) -> TokenDenylist:
    """Redis-backed when a client is supplied (production), else in-memory."""
    return RedisTokenDenylist(redis) if redis is not None else InMemoryTokenDenylist()
