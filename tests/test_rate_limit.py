"""Rate limiting on /auth/login.

The limiter is disabled for the rest of the suite (see conftest). Here we enable
it and confirm that bursting past the per-IP login limit yields 429 with a
Retry-After header — the brute-force / credential-stuffing defense.

These tests target the per-IP limiter specifically, so each request uses a
DISTINCT email. That keeps the independent per-account throttle (which locks an
email after repeated failures, see app.core.login_throttle) from firing first
and masking the IP-level behaviour under test.
"""

from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.rate_limit import limiter
from app.main import app


@pytest.fixture
async def client(session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def _override() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
def enabled_limiter() -> AsyncGenerator[None, None]:
    """Turn the limiter on with a clean window for this test only."""
    storage = getattr(limiter, "_storage", None)
    if storage is not None and hasattr(storage, "reset"):
        storage.reset()
    limiter.enabled = True
    yield
    limiter.enabled = False
    if storage is not None and hasattr(storage, "reset"):
        storage.reset()


# The configured limit is 10/minute; the 11th request from the same IP trips it.
_LOGIN_LIMIT_COUNT = 10


async def test_login_is_rate_limited_after_burst(
    client: AsyncClient, enabled_limiter: None
) -> None:
    # Distinct email per request so the per-account throttle never trips; only the
    # per-IP limiter is under test here.
    def _payload(i: int) -> dict[str, str]:
        return {"email": f"nobody{i}@test.example.com", "password": "wrong-password"}

    # The first N requests are processed (wrong credentials → 401), never 429.
    for i in range(_LOGIN_LIMIT_COUNT):
        res = await client.post("/api/v1/auth/login", json=_payload(i))
        assert res.status_code != 429

    # The next one exceeds the per-IP window.
    blocked = await client.post("/api/v1/auth/login", json=_payload(_LOGIN_LIMIT_COUNT))
    assert blocked.status_code == 429
    assert "retry-after" in {k.lower() for k in blocked.headers}


async def test_login_not_limited_when_disabled(
    client: AsyncClient, session: AsyncSession
) -> None:
    # With the IP limiter off (suite default), bursting never yields 429. Distinct
    # emails keep the per-account throttle out of the picture too.
    for i in range(_LOGIN_LIMIT_COUNT + 5):
        payload = {"email": f"nobody2-{i}@test.example.com", "password": "wrong-password"}
        res = await client.post("/api/v1/auth/login", json=payload)
        assert res.status_code != 429
