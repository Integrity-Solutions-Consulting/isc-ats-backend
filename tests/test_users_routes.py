"""Tests for auth.users endpoints.

Guards, roles in list response, PATCH activate/deactivate, self-deactivation guard.
All tests use the rolled-back session fixture from conftest.py — create your own data.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.security import create_access_token
from app.main import app
from app.modules.auth.application.bootstrap_service import (
    assign_role_to_user,
    bootstrap_admin,
)
from app.modules.auth.infrastructure.models import User
from app.modules.auth.infrastructure.repository import UserRepository
from app.modules.org.infrastructure.parameters_repository import ParameterRepository

USERS_URL = "/api/v1/auth/users"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def client(session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def _use_test_session() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_session] = _use_test_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


def _bearer(user_id: int, portal: str = "staff") -> dict[str, str]:
    token = create_access_token(user_id, extra_claims={"portal": portal})
    return {"Authorization": f"Bearer {token}"}


async def _staff_portal_id(session: AsyncSession) -> int:
    portal = await ParameterRepository(session).get_by_type_and_code("user_portal", "staff")
    assert portal is not None, "user_portal:staff seed missing"
    return portal.id


async def _make_user(session: AsyncSession, *, email: str | None = None) -> User:
    """Create an active staff user with no roles."""
    portal_id = await _staff_portal_id(session)
    return await UserRepository(session).add(
        User(
            email=email or f"{uuid.uuid4().hex[:12]}@test.local",
            portal_id=portal_id,
        )
    )


async def _find_user_in_list(
    client: AsyncClient, headers: dict[str, str], user_id: int
) -> dict[str, Any] | None:
    """Locate a user in the paginated list response.

    The list is ordered by id ascending and the shared dev DB holds thousands
    of users, so a freshly created user (highest id) lands on the LAST page —
    never assume page 1. Walk the last two pages to tolerate rows created by
    sibling fixtures in the same transaction.
    """
    first = await client.get(USERS_URL, params={"size": 100}, headers=headers)
    assert first.status_code == 200
    total = first.json()["total"]
    last_page = max(1, -(-total // 100))
    for page in (last_page, last_page - 1):
        if page < 1:
            break
        response = await client.get(
            USERS_URL, params={"size": 100, "page": page}, headers=headers
        )
        assert response.status_code == 200
        found = next((u for u in response.json()["items"] if u["id"] == user_id), None)
        if found is not None:
            return found
    return None


# ---------------------------------------------------------------------------
# Guard tests — auth.users.read
# ---------------------------------------------------------------------------


async def test_list_users_rejects_missing_token(client: AsyncClient) -> None:
    response = await client.get(USERS_URL)
    assert response.status_code == 401


async def test_list_users_forbids_user_without_permission(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await _make_user(session)
    response = await client.get(USERS_URL, headers=_bearer(user.id))
    assert response.status_code == 403
    assert "auth.users.read" in response.json()["detail"]


async def test_list_users_allows_admin(
    client: AsyncClient, session: AsyncSession
) -> None:
    admin = await bootstrap_admin(session, f"{uuid.uuid4().hex[:12]}@test.local", "S3cret")
    response = await client.get(USERS_URL, headers=_bearer(admin.user_id))
    assert response.status_code == 200
    assert "items" in response.json()


# ---------------------------------------------------------------------------
# Role names in list response
# ---------------------------------------------------------------------------


async def test_list_users_includes_role_names(
    client: AsyncClient, session: AsyncSession
) -> None:
    """A user with an assigned active role must appear with that role name in the list."""
    admin = await bootstrap_admin(session, f"{uuid.uuid4().hex[:12]}@test.local", "S3cret")

    # Find the admin role that was just bootstrapped (name="Administrador")
    from sqlalchemy import select
    from app.modules.auth.infrastructure.models import Role

    role = (
        await session.execute(
            select(Role).where(Role.name == "Administrador").where(Role.is_active.is_(True))
        )
    ).scalar_one()

    # Create a target user and assign the admin role to it
    target = await _make_user(session)
    await assign_role_to_user(session, target.id, role.id)

    found = await _find_user_in_list(client, _bearer(admin.user_id), target.id)
    assert found is not None, "Created user not found in list"
    assert "Administrador" in found["roles"]


async def test_list_users_has_empty_roles_when_no_assignment(
    client: AsyncClient, session: AsyncSession
) -> None:
    """A user without any role assignment returns roles=[]."""
    admin = await bootstrap_admin(session, f"{uuid.uuid4().hex[:12]}@test.local", "S3cret")
    target = await _make_user(session)

    found = await _find_user_in_list(client, _bearer(admin.user_id), target.id)
    assert found is not None
    assert found["roles"] == []


# ---------------------------------------------------------------------------
# Guard tests — PATCH auth.users.update
# ---------------------------------------------------------------------------


async def test_patch_user_rejects_missing_token(client: AsyncClient, session: AsyncSession) -> None:
    target = await _make_user(session)
    response = await client.patch(f"{USERS_URL}/{target.id}", json={"is_active": False})
    assert response.status_code == 401


async def test_patch_user_forbids_user_without_permission(
    client: AsyncClient, session: AsyncSession
) -> None:
    actor = await _make_user(session)
    target = await _make_user(session)
    response = await client.patch(
        f"{USERS_URL}/{target.id}",
        json={"is_active": False},
        headers=_bearer(actor.id),
    )
    assert response.status_code == 403
    assert "auth.users.update" in response.json()["detail"]


# ---------------------------------------------------------------------------
# PATCH functional tests
# ---------------------------------------------------------------------------


async def test_patch_deactivates_user(
    client: AsyncClient, session: AsyncSession
) -> None:
    admin = await bootstrap_admin(session, f"{uuid.uuid4().hex[:12]}@test.local", "S3cret")
    target = await _make_user(session)
    assert target.is_active is True

    response = await client.patch(
        f"{USERS_URL}/{target.id}",
        json={"is_active": False},
        headers=_bearer(admin.user_id),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == target.id
    assert body["is_active"] is False


async def test_patch_activates_user(
    client: AsyncClient, session: AsyncSession
) -> None:
    admin = await bootstrap_admin(session, f"{uuid.uuid4().hex[:12]}@test.local", "S3cret")
    portal_id = await _staff_portal_id(session)
    inactive_user = await UserRepository(session).add(
        User(
            email=f"{uuid.uuid4().hex[:12]}@test.local",
            portal_id=portal_id,
            is_active=False,
        )
    )

    response = await client.patch(
        f"{USERS_URL}/{inactive_user.id}",
        json={"is_active": True},
        headers=_bearer(admin.user_id),
    )
    assert response.status_code == 200
    assert response.json()["is_active"] is True


async def test_patch_returns_404_for_nonexistent_user(
    client: AsyncClient, session: AsyncSession
) -> None:
    admin = await bootstrap_admin(session, f"{uuid.uuid4().hex[:12]}@test.local", "S3cret")
    response = await client.patch(
        f"{USERS_URL}/999999999",
        json={"is_active": False},
        headers=_bearer(admin.user_id),
    )
    assert response.status_code == 404


async def test_patch_returns_400_when_self_deactivation(
    client: AsyncClient, session: AsyncSession
) -> None:
    admin = await bootstrap_admin(session, f"{uuid.uuid4().hex[:12]}@test.local", "S3cret")
    response = await client.patch(
        f"{USERS_URL}/{admin.user_id}",
        json={"is_active": False},
        headers=_bearer(admin.user_id),
    )
    assert response.status_code == 400
    assert "self" in response.json()["detail"].lower() or "propi" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# POST /auth/users — create staff user
# ---------------------------------------------------------------------------


async def test_create_user_requires_auth(client: AsyncClient) -> None:
    response = await client.post(USERS_URL, json={"email": "x@x.com", "password": "Abc123", "role_id": 1})
    assert response.status_code == 401


async def test_create_user_requires_permission(
    client: AsyncClient, session: AsyncSession
) -> None:
    """A user without auth.users.create must receive 403."""
    actor = await _make_user(session)
    response = await client.post(
        USERS_URL,
        json={"email": f"{uuid.uuid4().hex[:8]}@x.com", "password": "Abc1234!", "role_id": 1},
        headers=_bearer(actor.id),
    )
    assert response.status_code == 403
    assert "auth.users.create" in response.json()["detail"]


async def test_create_user_success(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Admin can create a staff user; response contains id, email, roles."""
    from app.modules.auth.infrastructure.models import Role
    from sqlalchemy import select

    admin = await bootstrap_admin(session, f"{uuid.uuid4().hex[:12]}@test.local", "S3cret")

    role = (
        await session.execute(
            select(Role).where(Role.name == "Administrador").where(Role.is_active.is_(True))
        )
    ).scalar_one()

    new_email = f"{uuid.uuid4().hex[:10]}@example.com"
    response = await client.post(
        USERS_URL,
        json={"email": new_email, "password": "Abc1234!", "role_id": role.id},
        headers=_bearer(admin.user_id),
    )
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == new_email
    assert body["is_active"] is True
    assert "Administrador" in body["roles"]
    assert body["id"] > 0


async def test_create_user_rejects_duplicate_email(
    client: AsyncClient, session: AsyncSession
) -> None:
    from app.modules.auth.infrastructure.models import Role
    from sqlalchemy import select

    admin = await bootstrap_admin(session, f"{uuid.uuid4().hex[:12]}@test.local", "S3cret")

    role = (
        await session.execute(
            select(Role).where(Role.name == "Administrador").where(Role.is_active.is_(True))
        )
    ).scalar_one()

    email = f"{uuid.uuid4().hex[:10]}@example.com"
    payload = {"email": email, "password": "Abc1234!", "role_id": role.id}

    r1 = await client.post(USERS_URL, json=payload, headers=_bearer(admin.user_id))
    assert r1.status_code == 201

    r2 = await client.post(USERS_URL, json=payload, headers=_bearer(admin.user_id))
    assert r2.status_code == 409


async def test_create_user_rejects_duplicate_email_case_insensitive(
    client: AsyncClient, session: AsyncSession
) -> None:
    """A differently-cased duplicate email must still be rejected with 409."""
    from app.modules.auth.infrastructure.models import Role
    from sqlalchemy import select

    admin = await bootstrap_admin(session, f"{uuid.uuid4().hex[:12]}@test.local", "S3cret")
    role = (
        await session.execute(
            select(Role).where(Role.name == "Administrador").where(Role.is_active.is_(True))
        )
    ).scalar_one()

    local = uuid.uuid4().hex[:10]
    payload_lower = {"email": f"{local}@example.com", "password": "Abc1234!", "role_id": role.id}
    payload_upper = {"email": f"{local.upper()}@EXAMPLE.com", "password": "Abc1234!", "role_id": role.id}

    r1 = await client.post(USERS_URL, json=payload_lower, headers=_bearer(admin.user_id))
    assert r1.status_code == 201

    r2 = await client.post(USERS_URL, json=payload_upper, headers=_bearer(admin.user_id))
    assert r2.status_code == 409


async def test_create_user_without_password_generates_and_emails_one(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Omitting the password generates a secure one, flags a forced change, and
    emails it to the new user.

    The email is enqueued via an awaited coroutine — a bare (un-awaited) call
    would silently drop it, so this test also guards that regression.
    """

    class _RecordingTaskQueue:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []

        async def enqueue(self, task_name: str, *args: object) -> None:
            self.calls.append((task_name, args))

    from sqlalchemy import select

    from app.modules.auth.infrastructure.models import Role

    admin = await bootstrap_admin(session, f"{uuid.uuid4().hex[:12]}@test.local", "S3cret")
    role = (
        await session.execute(
            select(Role).where(Role.name == "Administrador").where(Role.is_active.is_(True))
        )
    ).scalar_one()

    recorder = _RecordingTaskQueue()
    app.state.task_queue = recorder
    email = f"{uuid.uuid4().hex[:10]}@example.com"

    response = await client.post(
        USERS_URL,
        json={"email": email, "role_id": role.id},
        headers=_bearer(admin.user_id),
    )
    assert response.status_code == 201

    # The temp-password email must have been enqueued (proves the await is present).
    assert any(
        name == "send_random_password_email" and args[0] == email
        for name, args in recorder.calls
    ), "temp-password email must be enqueued when no password is provided"

    # The account is created with a usable hash and forced to rotate on first login.
    created = (
        await session.execute(select(User).where(User.email == email))
    ).scalar_one()
    assert created.password_hash is not None
    assert created.must_change_password is True


async def test_patch_deactivation_revokes_refresh_tokens(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Deactivating a user via PATCH must revoke all their active refresh tokens."""
    import uuid as _uuid
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from app.core.config import settings
    from app.core.security import hash_token
    from app.modules.auth.infrastructure.models import RefreshToken

    admin = await bootstrap_admin(session, f"{uuid.uuid4().hex[:12]}@test.local", "S3cret")
    target = await _make_user(session)

    expires_at = datetime.now(UTC) + timedelta(days=settings.refresh_token_expire_days)
    session.add(
        RefreshToken(
            user_id=target.id,
            token_hash=hash_token(_uuid.uuid4().hex),
            expires_at=expires_at,
            ip_address="127.0.0.1",
            created_by=target.id,
            ip_created="127.0.0.1",
        )
    )
    await session.flush()

    response = await client.patch(
        f"{USERS_URL}/{target.id}",
        json={"is_active": False},
        headers=_bearer(admin.user_id),
    )
    assert response.status_code == 200

    active = list(
        (
            await session.execute(
                select(RefreshToken)
                .where(RefreshToken.user_id == target.id)
                .where(RefreshToken.revoked_at.is_(None))
            )
        )
        .scalars()
        .all()
    )
    assert active == []


async def test_patch_reactivation_resends_welcome_email_if_never_logged_in(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Reactivating a user who never logged in (still on their original,
    possibly-never-received temp password) generates a fresh password and
    resends the welcome email — the original invite may never have arrived."""

    class _RecordingTaskQueue:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []

        async def enqueue(self, task_name: str, *args: object) -> None:
            self.calls.append((task_name, args))

    admin = await bootstrap_admin(session, f"{uuid.uuid4().hex[:12]}@test.local", "S3cret")
    portal_id = await _staff_portal_id(session)
    target = await UserRepository(session).add(
        User(
            email=f"{uuid.uuid4().hex[:12]}@test.local",
            portal_id=portal_id,
            is_active=False,
            must_change_password=True,
            last_login_at=None,
        )
    )
    original_hash = target.password_hash

    recorder = _RecordingTaskQueue()
    app.state.task_queue = recorder

    response = await client.patch(
        f"{USERS_URL}/{target.id}",
        json={"is_active": True},
        headers=_bearer(admin.user_id),
    )
    assert response.status_code == 200

    assert any(
        name == "send_random_password_email" and args[0] == target.email
        for name, args in recorder.calls
    ), "welcome email must be resent when reactivating a never-logged-in user"

    await session.refresh(target)
    assert target.password_hash != original_hash


async def test_patch_reactivation_does_not_resend_email_if_already_used(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Reactivating a user who already logged in before must NOT overwrite
    their real password or resend a temp-password email."""

    class _RecordingTaskQueue:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []

        async def enqueue(self, task_name: str, *args: object) -> None:
            self.calls.append((task_name, args))

    from datetime import UTC, datetime

    admin = await bootstrap_admin(session, f"{uuid.uuid4().hex[:12]}@test.local", "S3cret")
    portal_id = await _staff_portal_id(session)
    target = await UserRepository(session).add(
        User(
            email=f"{uuid.uuid4().hex[:12]}@test.local",
            portal_id=portal_id,
            is_active=False,
            must_change_password=False,
            last_login_at=datetime.now(UTC),
        )
    )
    original_hash = target.password_hash

    recorder = _RecordingTaskQueue()
    app.state.task_queue = recorder

    response = await client.patch(
        f"{USERS_URL}/{target.id}",
        json={"is_active": True},
        headers=_bearer(admin.user_id),
    )
    assert response.status_code == 200

    assert not any(
        name == "send_random_password_email" for name, _args in recorder.calls
    ), "reactivating an already-onboarded user must not resend a temp-password email"

    await session.refresh(target)
    assert target.password_hash == original_hash


async def test_create_user_rejects_invalid_role(
    client: AsyncClient, session: AsyncSession
) -> None:
    admin = await bootstrap_admin(session, f"{uuid.uuid4().hex[:12]}@test.local", "S3cret")
    response = await client.post(
        USERS_URL,
        json={"email": f"{uuid.uuid4().hex[:8]}@example.com", "password": "Abc1234!", "role_id": 999999},
        headers=_bearer(admin.user_id),
    )
    assert response.status_code == 400
    assert "Role" in response.json()["detail"]


async def test_created_user_can_login(
    client: AsyncClient, session: AsyncSession
) -> None:
    """A user created via POST /auth/users can authenticate via POST /auth/login."""
    from app.modules.auth.infrastructure.models import Role
    from sqlalchemy import select

    admin = await bootstrap_admin(session, f"{uuid.uuid4().hex[:12]}@test.local", "S3cret")

    role = (
        await session.execute(
            select(Role).where(Role.name == "Administrador").where(Role.is_active.is_(True))
        )
    ).scalar_one()

    new_email = f"{uuid.uuid4().hex[:10]}@example.com"
    pw = "StaffPass99!"
    r = await client.post(
        USERS_URL,
        json={"email": new_email, "password": pw, "role_id": role.id},
        headers=_bearer(admin.user_id),
    )
    assert r.status_code == 201

    login_r = await client.post(
        "/api/v1/auth/login",
        json={"email": new_email, "password": pw},
    )
    assert login_r.status_code == 200
    login_body = login_r.json()
    # User was created with the staff portal → portal claim must be "staff"
    assert login_body["portal"] == "staff"
    assert "access_token" in login_body
