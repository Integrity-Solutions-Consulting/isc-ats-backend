"""Tests for DELETE /auth/me — candidate self-deactivation endpoint.

Covers:
- Candidate user self-deletes → 204, user.is_active=False, candidate is_active=False,
  all refresh tokens revoked.
- Staff user gets 403 (no admin lockout).
- Login after deactivation fails (get_by_email filters is_active=True).
- No candidate profile → still 204 (no-op on profile step).
"""

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.security import (
    create_access_token,
    hash_password,
    hash_token,
)
from app.core.config import settings
from app.main import app
from app.modules.auth.infrastructure.models import RefreshToken, User
from app.modules.auth.infrastructure.repository import UserRepository
from app.modules.org.infrastructure.parameters_repository import ParameterRepository
from app.modules.recruitment.infrastructure.candidate_models import Candidate
from app.modules.recruitment.infrastructure.candidates_repository import CandidateRepository


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
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


async def _staff_portal_id(session: AsyncSession) -> int:
    portal = await ParameterRepository(session).get_by_type_and_code("user_portal", "staff")
    assert portal is not None, "user_portal:staff must be seeded"
    return portal.id


async def _make_candidate_user(session: AsyncSession) -> User:
    portal_id = await _candidate_portal_id(session)
    return await UserRepository(session).add(
        User(
            email=f"cand-{uuid.uuid4().hex[:12]}@test.example.com",
            password_hash=hash_password("Pass1234!"),
            portal_id=portal_id,
            email_verified=True,
        )
    )


async def _make_staff_user(session: AsyncSession) -> User:
    portal_id = await _staff_portal_id(session)
    return await UserRepository(session).add(
        User(
            email=f"staff-{uuid.uuid4().hex[:12]}@test.example.com",
            password_hash=hash_password("Pass1234!"),
            portal_id=portal_id,
            email_verified=True,
        )
    )


async def _make_candidate_profile(session: AsyncSession, user: User) -> Candidate:
    candidate = Candidate(
        user_id=user.id,
        first_name="Test",
        last_name="Candidate",
    )
    return await CandidateRepository(session).add(candidate)


async def _issue_refresh_token(session: AsyncSession, user: User) -> str:
    # Use a random raw value to avoid hash collisions when called twice in the same ms.
    raw = uuid.uuid4().hex
    expires_at = datetime.now(UTC) + timedelta(days=settings.refresh_token_expire_days)
    token = RefreshToken(
        user_id=user.id,
        token_hash=hash_token(raw),
        expires_at=expires_at,
        ip_address="127.0.0.1",
        created_by=user.id,
        ip_created="127.0.0.1",
    )
    session.add(token)
    await session.flush()
    return raw


def _bearer(user_id: int, portal: str) -> dict[str, str]:
    token = create_access_token(user_id, extra_claims={"portal": portal})
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_candidate_self_delete_returns_204(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await _make_candidate_user(session)
    await _make_candidate_profile(session, user)

    response = await client.delete("/api/v1/auth/me", headers=_bearer(user.id, "candidate"))

    assert response.status_code == 204


async def test_candidate_self_delete_deactivates_user(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await _make_candidate_user(session)
    await _make_candidate_profile(session, user)

    await client.delete("/api/v1/auth/me", headers=_bearer(user.id, "candidate"))

    # Reload without is_active filter
    refreshed = await session.get(User, user.id)
    assert refreshed is not None
    assert refreshed.is_active is False


async def test_candidate_self_delete_deactivates_candidate_profile(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await _make_candidate_user(session)
    candidate = await _make_candidate_profile(session, user)

    await client.delete("/api/v1/auth/me", headers=_bearer(user.id, "candidate"))

    refreshed = await session.get(Candidate, candidate.id)
    assert refreshed is not None
    assert refreshed.is_active is False


async def test_candidate_self_delete_revokes_refresh_tokens(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await _make_candidate_user(session)
    await _make_candidate_profile(session, user)
    # Issue two refresh tokens for this user
    await _issue_refresh_token(session, user)
    await _issue_refresh_token(session, user)

    await client.delete("/api/v1/auth/me", headers=_bearer(user.id, "candidate"))

    # All tokens for this user must now be revoked
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


async def test_staff_user_gets_403(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await _make_staff_user(session)

    response = await client.delete("/api/v1/auth/me", headers=_bearer(user.id, "staff"))

    assert response.status_code == 403


async def test_login_after_deactivation_fails(
    client: AsyncClient, session: AsyncSession
) -> None:
    """UserRepository.get_by_email filters is_active=True, so login should return 401."""
    user = await _make_candidate_user(session)
    await _make_candidate_profile(session, user)
    email = user.email

    await client.delete("/api/v1/auth/me", headers=_bearer(user.id, "candidate"))

    response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "Pass1234!"},
    )
    assert response.status_code == 401


async def test_no_candidate_profile_still_204(
    client: AsyncClient, session: AsyncSession
) -> None:
    """A candidate user with no profile can still self-deactivate; profile step is a no-op."""
    user = await _make_candidate_user(session)
    # Deliberately do NOT create a candidate profile

    response = await client.delete("/api/v1/auth/me", headers=_bearer(user.id, "candidate"))

    assert response.status_code == 204
    refreshed = await session.get(User, user.id)
    assert refreshed is not None
    assert refreshed.is_active is False
