"""Unit tests for the in-memory access-token denylist (security fix 3.4).

The denylist records a per-user "revoked before" cutoff. An access token is
revoked when its `iat` is at or before that cutoff (so a stolen token issued
before a password change dies immediately). The marker self-expires after the
access-token lifetime.
"""

import time

from app.core.token_denylist import InMemoryTokenDenylist


async def test_unknown_user_is_not_revoked() -> None:
    denylist = InMemoryTokenDenylist()
    assert await denylist.is_user_revoked(user_id=1, issued_at=int(time.time())) is False


async def test_token_issued_before_cutoff_is_revoked() -> None:
    denylist = InMemoryTokenDenylist()
    issued_at = int(time.time()) - 5
    await denylist.revoke_user(user_id=1, ttl_seconds=1800)

    assert await denylist.is_user_revoked(user_id=1, issued_at=issued_at) is True


async def test_token_issued_at_cutoff_is_revoked() -> None:
    """Same-second boundary: a token issued in the revocation second must die.

    Inclusive comparison closes the sub-second window where a compromised token
    minted in the same second as the password change would otherwise survive.
    """
    denylist = InMemoryTokenDenylist()
    await denylist.revoke_user(user_id=1, ttl_seconds=1800)

    assert await denylist.is_user_revoked(user_id=1, issued_at=int(time.time())) is True


async def test_token_issued_after_cutoff_is_not_revoked() -> None:
    denylist = InMemoryTokenDenylist()
    await denylist.revoke_user(user_id=1, ttl_seconds=1800)

    future_iat = int(time.time()) + 5
    assert await denylist.is_user_revoked(user_id=1, issued_at=future_iat) is False


async def test_other_users_are_unaffected() -> None:
    denylist = InMemoryTokenDenylist()
    await denylist.revoke_user(user_id=1, ttl_seconds=1800)

    assert await denylist.is_user_revoked(user_id=2, issued_at=int(time.time())) is False


async def test_marker_expires_after_ttl() -> None:
    """A zero TTL is already expired, so the marker no longer revokes anything."""
    denylist = InMemoryTokenDenylist()
    issued_at = int(time.time()) - 5
    await denylist.revoke_user(user_id=1, ttl_seconds=0)

    assert await denylist.is_user_revoked(user_id=1, issued_at=issued_at) is False
