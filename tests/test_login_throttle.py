"""Unit tests for the in-memory login throttle (per-account brute-force lock)."""

import pytest

from app.core.login_throttle import (
    MAX_FAILED_ATTEMPTS,
    InMemoryLoginThrottle,
)

EMAIL = "victim@example.com"


async def test_not_locked_initially() -> None:
    throttle = InMemoryLoginThrottle()
    assert await throttle.locked_for(EMAIL) is None


async def test_locks_after_threshold_failures() -> None:
    throttle = InMemoryLoginThrottle()
    for _ in range(MAX_FAILED_ATTEMPTS):
        await throttle.record_failure(EMAIL)
    remaining = await throttle.locked_for(EMAIL)
    assert remaining is not None
    assert remaining > 0


async def test_below_threshold_does_not_lock() -> None:
    throttle = InMemoryLoginThrottle()
    for _ in range(MAX_FAILED_ATTEMPTS - 1):
        await throttle.record_failure(EMAIL)
    assert await throttle.locked_for(EMAIL) is None


async def test_reset_clears_failures() -> None:
    throttle = InMemoryLoginThrottle()
    for _ in range(MAX_FAILED_ATTEMPTS - 1):
        await throttle.record_failure(EMAIL)
    await throttle.reset(EMAIL)
    # After a reset, a single new failure must not trip the lock.
    await throttle.record_failure(EMAIL)
    assert await throttle.locked_for(EMAIL) is None


async def test_reset_clears_existing_lock() -> None:
    throttle = InMemoryLoginThrottle()
    for _ in range(MAX_FAILED_ATTEMPTS):
        await throttle.record_failure(EMAIL)
    assert await throttle.locked_for(EMAIL) is not None
    await throttle.reset(EMAIL)
    assert await throttle.locked_for(EMAIL) is None


async def test_accounts_are_independent() -> None:
    throttle = InMemoryLoginThrottle()
    for _ in range(MAX_FAILED_ATTEMPTS):
        await throttle.record_failure("attacked@example.com")
    assert await throttle.locked_for("attacked@example.com") is not None
    assert await throttle.locked_for("bystander@example.com") is None


@pytest.mark.parametrize(
    "recorded, checked",
    [
        ("User@Example.com", "user@example.com"),
        ("  user@example.com  ", "user@example.com"),
    ],
)
async def test_email_is_normalized(recorded: str, checked: str) -> None:
    throttle = InMemoryLoginThrottle()
    for _ in range(MAX_FAILED_ATTEMPTS):
        await throttle.record_failure(recorded)
    # Case and surrounding whitespace must not let an attacker dodge the lock.
    assert await throttle.locked_for(checked) is not None
