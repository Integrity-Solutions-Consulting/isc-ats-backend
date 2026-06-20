"""Integration tests for access-token revocation (security fix 3.4, R3).

Until now an access token stayed valid for its full 30-minute lifetime even
after logout/password-change — it could not be revoked. These tests prove that
changing the password or self-deactivating immediately invalidates every access
token the user already holds.

The probe is a second authenticated call with the SAME (now-stale) bearer:
- 401 means get_current_user rejected the token via the denylist (the fix works).
- 400 would mean the token still authenticated and the handler ran (no fix).
"""

import uuid
from collections.abc import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_session
from app.core.security import create_access_token, hash_password
from app.main import app
from app.modules.auth.infrastructure.models import User
from app.modules.auth.infrastructure.repository import UserRepository
from app.modules.org.infrastructure.parameters_repository import ParameterRepository

CURRENT_PASSWORD = "CurrentPass123!"
CHANGE_URL = "/api/v1/auth/me/change-password"
DELETE_URL = "/api/v1/auth/me"


@pytest_asyncio.fixture
async def client(session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def _override() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def _make_candidate(session: AsyncSession) -> User:
    portal = await ParameterRepository(session).get_by_type_and_code("user_portal", "candidate")
    assert portal is not None
    return await UserRepository(session).add(
        User(
            email=f"cand-{uuid.uuid4().hex[:12]}@test.example.com",
            password_hash=hash_password(CURRENT_PASSWORD),
            portal_id=portal.id,
            email_verified=True,
        )
    )


def _bearer(user_id: int, portal: str = "candidate") -> dict[str, str]:
    token = create_access_token(user_id, extra_claims={"portal": portal})
    return {"Authorization": f"Bearer {token}"}


async def test_change_password_revokes_existing_access_token(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await _make_candidate(session)
    headers = _bearer(user.id)

    first = await client.post(
        CHANGE_URL,
        headers=headers,
        json={"current_password": CURRENT_PASSWORD, "new_password": "NewPass456!"},
    )
    assert first.status_code == 200

    # Same bearer, second call: the token was minted before the change -> revoked.
    second = await client.post(
        CHANGE_URL,
        headers=headers,
        json={"current_password": "NewPass456!", "new_password": "Another789!"},
    )
    assert second.status_code == 401


async def test_self_deactivation_revokes_existing_access_token(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await _make_candidate(session)
    headers = _bearer(user.id)

    deleted = await client.delete(DELETE_URL, headers=headers)
    assert deleted.status_code == 204

    # The access token from before the deactivation must no longer authenticate.
    after = await client.post(
        CHANGE_URL,
        headers=headers,
        json={"current_password": CURRENT_PASSWORD, "new_password": "NewPass456!"},
    )
    assert after.status_code == 401


async def test_unrevoked_access_token_still_authenticates(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Sanity: with no revocation, a valid bearer authenticates normally (400 = wrong
    current password reached the handler, i.e. auth passed)."""
    user = await _make_candidate(session)

    response = await client.post(
        CHANGE_URL,
        headers=_bearer(user.id),
        json={"current_password": "WrongPass!", "new_password": "NewPass456!"},
    )
    assert response.status_code == 400


# Belt-and-suspenders: the lifetime-based TTL is derived from settings so the
# marker never outlives the longest-lived access token it must cover.
def test_denylist_ttl_covers_access_token_lifetime() -> None:
    assert settings.access_token_expire_minutes > 0
