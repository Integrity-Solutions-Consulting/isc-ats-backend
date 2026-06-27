"""Per-account login throttle (brute-force / credential-stuffing defense).

The slowapi rate limiter (app.core.rate_limit) caps requests per CLIENT IP. That
stops one machine hammering /login, but not a distributed attack against a single
account, and it cannot tell a failed attempt from a successful one. This throttle
adds the account dimension: too many FAILED logins for one email within a short
window temporarily locks that email, regardless of source IP.

Design choices that matter:
- Only FAILURES count, and a successful login resets the counter — a user who
  mistypes a couple of times never hits the limit.
- The lock is short (15 min) and auto-expires. A permanent lock would let anyone
  who knows a victim's email lock them out at will (lockout DoS). The goal is to
  make brute force too slow to be useful, not to punish.
- Emails are hashed before becoming keys, so raw addresses never sit in Redis.

Two adapters mirror token_denylist: in-memory (dev/test, single process) and
Redis (production, shared across API replicas).
"""

from __future__ import annotations

import hashlib
import time
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from redis.asyncio import Redis

# Policy — tune here. Mirrors the named-limits convention in rate_limit.py.
MAX_FAILED_ATTEMPTS = 5
# Window over which failures accumulate toward a lock.
FAILURE_WINDOW_SECONDS = 15 * 60
# How long the account stays locked once the threshold is reached.
LOCKOUT_SECONDS = 15 * 60


def _key_suffix(email: str) -> str:
    """Hash the normalized email so raw addresses never become keys."""
    normalized = email.strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class LoginThrottle(Protocol):
    """Port: track failed logins per account and report / lift the lock."""

    async def locked_for(self, email: str) -> int | None:
        """Seconds remaining on the lock, or None if the account is not locked."""
        ...

    async def record_failure(self, email: str) -> None:
        """Count one failed attempt; lock the account when the threshold is hit."""
        ...

    async def reset(self, email: str) -> None:
        """Clear failures and any lock — called after a successful login."""
        ...


class InMemoryLoginThrottle:
    """Process-local throttle. Fine for tests and single-process dev."""

    def __init__(self) -> None:
        # suffix -> (failure_count, window_expires_at_monotonic)
        self._failures: dict[str, tuple[int, float]] = {}
        # suffix -> lock_expires_at_monotonic
        self._locks: dict[str, float] = {}

    async def locked_for(self, email: str) -> int | None:
        key = _key_suffix(email)
        expires_at = self._locks.get(key)
        if expires_at is None:
            return None
        remaining = expires_at - time.monotonic()
        if remaining <= 0:
            del self._locks[key]  # lazy cleanup of an expired lock
            return None
        return int(remaining) + 1  # round up so a live lock never reports 0

    async def record_failure(self, email: str) -> None:
        key = _key_suffix(email)
        now = time.monotonic()
        count, window_expires_at = self._failures.get(key, (0, 0.0))
        if now >= window_expires_at:
            count = 0  # window elapsed — start a fresh count
            window_expires_at = now + FAILURE_WINDOW_SECONDS
        count += 1
        self._failures[key] = (count, window_expires_at)
        if count >= MAX_FAILED_ATTEMPTS:
            self._locks[key] = now + LOCKOUT_SECONDS
            del self._failures[key]  # the lock supersedes the counter

    async def reset(self, email: str) -> None:
        key = _key_suffix(email)
        self._failures.pop(key, None)
        self._locks.pop(key, None)


class RedisLoginThrottle:
    """Distributed throttle — auto-expiring keys shared across API replicas."""

    _FAIL_PREFIX = "login_fail:"
    _LOCK_PREFIX = "login_lock:"

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def locked_for(self, email: str) -> int | None:
        ttl = await self._redis.ttl(f"{self._LOCK_PREFIX}{_key_suffix(email)}")
        # redis TTL: -2 = no key, -1 = key without expiry. Both mean "not locked".
        return ttl if ttl > 0 else None

    async def record_failure(self, email: str) -> None:
        suffix = _key_suffix(email)
        fail_key = f"{self._FAIL_PREFIX}{suffix}"
        count = await self._redis.incr(fail_key)
        if count == 1:
            # First failure opens the window; the counter self-expires with it.
            await self._redis.expire(fail_key, FAILURE_WINDOW_SECONDS)
        if count >= MAX_FAILED_ATTEMPTS:
            await self._redis.set(f"{self._LOCK_PREFIX}{suffix}", 1, ex=LOCKOUT_SECONDS)
            await self._redis.delete(fail_key)

    async def reset(self, email: str) -> None:
        suffix = _key_suffix(email)
        await self._redis.delete(
            f"{self._FAIL_PREFIX}{suffix}", f"{self._LOCK_PREFIX}{suffix}"
        )


def build_login_throttle(redis: Redis | None = None) -> LoginThrottle:
    """Redis-backed when a client is supplied (production), else in-memory."""
    return RedisLoginThrottle(redis) if redis is not None else InMemoryLoginThrottle()
