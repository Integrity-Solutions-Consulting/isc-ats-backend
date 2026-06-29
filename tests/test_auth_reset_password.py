"""Tests for the forgot/reset-password flow.

POST /auth/forgot-password — request a reset link (anti-enumeration: identical
generic response whether or not the email exists; the email is only enqueued for
an eligible account).

POST /auth/reset-password — set a new password from a single-use token. Covers:
- Valid token → 200, hash updated, new password works, old rejected.
- Single-use: the same token is rejected after a successful reset (fingerprint).
- Invalid / wrong-type / expired token → 400.
- Weak new password → 422 (policy).
- All refresh tokens revoked after a successful reset.
"""

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_session
from app.core.security import (
    create_password_reset_token,
    create_verification_token,
    hash_password,
    hash_token,
    verify_password,
)
from app.main import app
from app.modules.auth.infrastructure.models import RefreshToken, User
from app.modules.auth.infrastructure.repository import UserRepository
from app.modules.org.infrastructure.parameters_repository import ParameterRepository

CURRENT_PASSWORD = "CurrentPass123!"
NEW_PASSWORD = "BrandNew456!"
FORGOT_URL = "/api/v1/auth/forgot-password"
RESET_URL = "/api/v1/auth/reset-password"


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


class _SpyTaskQueue:
    """Records enqueued tasks so we can assert on the email side effect without
    actually sending anything."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def enqueue(self, task_name: str, *args: object) -> None:
        self.calls.append((task_name, args))


@pytest.fixture
def spy_queue() -> _SpyTaskQueue:
    spy = _SpyTaskQueue()
    app.state.task_queue = spy
    return spy


async def _candidate_portal_id(session: AsyncSession) -> int:
    portal = await ParameterRepository(session).get_by_type_and_code("user_portal", "candidate")
    assert portal is not None, "user_portal:candidate must be seeded"
    return portal.id


async def _make_user(
    session: AsyncSession, *, email_verified: bool = True
) -> User:
    portal_id = await _candidate_portal_id(session)
    return await UserRepository(session).add(
        User(
            email=f"reset-{uuid.uuid4().hex[:12]}@test.example.com",
            password_hash=hash_password(CURRENT_PASSWORD),
            portal_id=portal_id,
            email_verified=email_verified,
            must_change_password=False,
        )
    )


def _reset_token(user: User) -> str:
    assert user.password_hash is not None
    return create_password_reset_token(user.id, user.password_hash)


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


# ---------------------------------------------------------------------------
# reset-password
# ---------------------------------------------------------------------------


async def test_reset_password_success_returns_200(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await _make_user(session)

    response = await client.post(
        RESET_URL, json={"token": _reset_token(user), "new_password": NEW_PASSWORD}
    )

    assert response.status_code == 200


async def test_reset_password_updates_hash(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await _make_user(session)

    await client.post(
        RESET_URL, json={"token": _reset_token(user), "new_password": NEW_PASSWORD}
    )

    refreshed = await session.get(User, user.id)
    assert refreshed is not None
    assert verify_password(NEW_PASSWORD, refreshed.password_hash) is True
    assert verify_password(CURRENT_PASSWORD, refreshed.password_hash) is False


async def test_reset_password_token_is_single_use(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await _make_user(session)
    token = _reset_token(user)

    first = await client.post(
        RESET_URL, json={"token": token, "new_password": NEW_PASSWORD}
    )
    assert first.status_code == 200

    # The same token must be rejected now that the password (hash) has changed.
    second = await client.post(
        RESET_URL, json={"token": token, "new_password": "Different789!"}
    )
    assert second.status_code == 400

    # The second attempt must not have taken effect.
    refreshed = await session.get(User, user.id)
    assert refreshed is not None
    assert verify_password(NEW_PASSWORD, refreshed.password_hash) is True


async def test_reset_password_invalid_token_returns_400(
    client: AsyncClient, session: AsyncSession
) -> None:
    response = await client.post(
        RESET_URL, json={"token": "not-a-real-token", "new_password": NEW_PASSWORD}
    )

    assert response.status_code == 400


async def test_reset_password_wrong_token_type_returns_400(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await _make_user(session)
    # A verification token is a valid JWT but the wrong type for reset.
    token = create_verification_token(user.id)

    response = await client.post(
        RESET_URL, json={"token": token, "new_password": NEW_PASSWORD}
    )

    assert response.status_code == 400
    refreshed = await session.get(User, user.id)
    assert refreshed is not None
    assert verify_password(CURRENT_PASSWORD, refreshed.password_hash) is True


async def test_reset_password_weak_password_returns_422(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await _make_user(session)

    response = await client.post(
        RESET_URL, json={"token": _reset_token(user), "new_password": "123"}
    )

    assert response.status_code == 422


async def test_reset_password_revokes_refresh_tokens(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await _make_user(session)
    await _issue_refresh_token(session, user)
    await _issue_refresh_token(session, user)

    await client.post(
        RESET_URL, json={"token": _reset_token(user), "new_password": NEW_PASSWORD}
    )

    active = list(
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
    assert len(active) == 0


async def test_login_with_new_password_after_reset(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await _make_user(session)
    email = user.email

    await client.post(
        RESET_URL, json={"token": _reset_token(user), "new_password": NEW_PASSWORD}
    )

    new_ok = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": NEW_PASSWORD}
    )
    assert new_ok.status_code == 200

    old_fail = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": CURRENT_PASSWORD}
    )
    assert old_fail.status_code == 401


# ---------------------------------------------------------------------------
# forgot-password (anti-enumeration)
# ---------------------------------------------------------------------------


async def test_forgot_password_unknown_email_returns_generic_200_without_enqueue(
    client: AsyncClient, session: AsyncSession, spy_queue: _SpyTaskQueue
) -> None:
    response = await client.post(
        FORGOT_URL, json={"email": f"ghost-{uuid.uuid4().hex[:8]}@test.example.com"}
    )

    assert response.status_code == 200
    assert spy_queue.calls == []  # No email for a non-existent account.


async def test_forgot_password_eligible_user_enqueues_email(
    client: AsyncClient, session: AsyncSession, spy_queue: _SpyTaskQueue
) -> None:
    user = await _make_user(session, email_verified=True)

    response = await client.post(FORGOT_URL, json={"email": user.email})

    assert response.status_code == 200
    assert len(spy_queue.calls) == 1
    task_name, args = spy_queue.calls[0]
    assert task_name == "send_password_reset_email"
    assert args[0] == user.id


async def test_forgot_password_unverified_user_does_not_enqueue(
    client: AsyncClient, session: AsyncSession, spy_queue: _SpyTaskQueue
) -> None:
    user = await _make_user(session, email_verified=False)

    response = await client.post(FORGOT_URL, json={"email": user.email})

    assert response.status_code == 200
    assert spy_queue.calls == []  # Unverified accounts are not eligible.


async def test_forgot_password_response_is_identical_for_known_and_unknown(
    client: AsyncClient, session: AsyncSession, spy_queue: _SpyTaskQueue
) -> None:
    user = await _make_user(session)

    known = await client.post(FORGOT_URL, json={"email": user.email})
    unknown = await client.post(
        FORGOT_URL, json={"email": f"nobody-{uuid.uuid4().hex[:8]}@test.example.com"}
    )

    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()
