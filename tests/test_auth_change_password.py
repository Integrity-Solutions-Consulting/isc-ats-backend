"""Tests for POST /auth/me/change-password — authenticated self password change.

Covers:
- Authenticated user changes password → 200, hash updated, new password works,
  old password rejected.
- Wrong current password → 400.
- New password too short → 422 (Pydantic min_length).
- New password equal to current → 400.
- Missing bearer → 401 (unauthenticated).
- All refresh tokens revoked after a successful change.
- must_change_password flag cleared.
"""

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_session
from app.core.security import (
    create_access_token,
    hash_password,
    hash_token,
    verify_password,
)
from app.main import app
from app.modules.auth.infrastructure.models import RefreshToken, User
from app.modules.auth.infrastructure.repository import UserRepository
from app.modules.org.infrastructure.parameters_repository import ParameterRepository

CURRENT_PASSWORD = "CurrentPass123!"
CHANGE_URL = "/api/v1/auth/me/change-password"


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client(session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def _override() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def _candidate_portal_id(session: AsyncSession) -> int:
    portal = await ParameterRepository(session).get_by_type_and_code("user_portal", "candidate")
    assert portal is not None, "user_portal:candidate must be seeded"
    return portal.id


async def _make_user(
    session: AsyncSession, *, must_change_password: bool = False
) -> User:
    portal_id = await _candidate_portal_id(session)
    return await UserRepository(session).add(
        User(
            email=f"cand-{uuid.uuid4().hex[:12]}@test.example.com",
            password_hash=hash_password(CURRENT_PASSWORD),
            portal_id=portal_id,
            email_verified=True,
            must_change_password=must_change_password,
        )
    )


async def _issue_refresh_token(session: AsyncSession, user: User) -> None:
    raw = uuid.uuid4().hex
    expires_at = datetime.now(UTC) + timedelta(days=settings.refresh_token_expire_days)
    session.add(
        RefreshToken(
            user_id=user.id,
            token_hash=hash_token(raw),
            expires_at=expires_at,
            ip_address="127.0.0.1",
            created_by=user.id,
            ip_created="127.0.0.1",
        )
    )
    await session.flush()


def _bearer(user_id: int, portal: str = "candidate") -> dict[str, str]:
    token = create_access_token(user_id, extra_claims={"portal": portal})
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_change_password_success_returns_200(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await _make_user(session)

    response = await client.post(
        CHANGE_URL,
        headers=_bearer(user.id),
        json={"current_password": CURRENT_PASSWORD, "new_password": "NewPass456!"},
    )

    assert response.status_code == 200


async def test_change_password_updates_hash(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await _make_user(session)

    await client.post(
        CHANGE_URL,
        headers=_bearer(user.id),
        json={"current_password": CURRENT_PASSWORD, "new_password": "NewPass456!"},
    )

    refreshed = await session.get(User, user.id)
    assert refreshed is not None
    assert refreshed.password_hash is not None
    assert verify_password("NewPass456!", refreshed.password_hash) is True
    assert verify_password(CURRENT_PASSWORD, refreshed.password_hash) is False


async def test_change_password_wrong_current_returns_400(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await _make_user(session)

    response = await client.post(
        CHANGE_URL,
        headers=_bearer(user.id),
        json={"current_password": "WrongPass!", "new_password": "NewPass456!"},
    )

    assert response.status_code == 400
    # The stored hash must remain the original.
    refreshed = await session.get(User, user.id)
    assert refreshed is not None
    assert verify_password(CURRENT_PASSWORD, refreshed.password_hash) is True


async def test_new_password_too_short_returns_422(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await _make_user(session)

    response = await client.post(
        CHANGE_URL,
        headers=_bearer(user.id),
        json={"current_password": CURRENT_PASSWORD, "new_password": "123"},
    )

    assert response.status_code == 422


async def test_new_password_same_as_current_returns_400(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await _make_user(session)

    response = await client.post(
        CHANGE_URL,
        headers=_bearer(user.id),
        json={"current_password": CURRENT_PASSWORD, "new_password": CURRENT_PASSWORD},
    )

    assert response.status_code == 400


async def test_change_password_requires_auth_returns_401(
    client: AsyncClient, session: AsyncSession
) -> None:
    response = await client.post(
        CHANGE_URL,
        json={"current_password": CURRENT_PASSWORD, "new_password": "NewPass456!"},
    )

    assert response.status_code == 401


async def test_change_password_revokes_refresh_tokens(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await _make_user(session)
    await _issue_refresh_token(session, user)
    await _issue_refresh_token(session, user)

    await client.post(
        CHANGE_URL,
        headers=_bearer(user.id),
        json={"current_password": CURRENT_PASSWORD, "new_password": "NewPass456!"},
    )

    active_tokens = list(
        (
            await session.execute(
                select(RefreshToken)
                .where(RefreshToken.user_id == user.id)
                .where(RefreshToken.revoked_at.is_(None))
            )
        )
        .scalars()
        .all()
    )
    assert len(active_tokens) == 0


async def test_change_password_clears_must_change_password(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await _make_user(session, must_change_password=True)

    await client.post(
        CHANGE_URL,
        headers=_bearer(user.id),
        json={"current_password": CURRENT_PASSWORD, "new_password": "NewPass456!"},
    )

    refreshed = await session.get(User, user.id)
    assert refreshed is not None
    assert refreshed.must_change_password is False


async def test_login_with_new_password_after_change(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await _make_user(session)
    email = user.email

    await client.post(
        CHANGE_URL,
        headers=_bearer(user.id),
        json={"current_password": CURRENT_PASSWORD, "new_password": "NewPass456!"},
    )

    new_ok = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "NewPass456!"}
    )
    assert new_ok.status_code == 200

    old_fail = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": CURRENT_PASSWORD}
    )
    assert old_fail.status_code == 401
