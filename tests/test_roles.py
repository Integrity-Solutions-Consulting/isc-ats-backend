"""Tests for auth.roles service and HTTP endpoints.

Service tests: pure ORM logic, rolled-back session.
HTTP tests: ASGI client with session override, no actual DB connections outside test tx.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.dependencies import CurrentUser
from app.core.security import create_access_token
from app.modules.auth.api.roles_schemas import RoleCreate, RoleUpdate
from app.modules.auth.application.bootstrap_service import bootstrap_admin
from app.modules.auth.application.roles_service import (
    RoleDuplicateError,
    RoleHasUsersError,
    RoleNotFoundError,
    RoleService,
    SystemRoleError,
)
from app.modules.auth.infrastructure.models import Role, UserRole
from app.main import app
from app.shared.pagination import PageParams
from app.shared.repository import BaseRepository

ACTOR = CurrentUser(user_id=1, ip="127.0.0.1")
ROLES_URL = "/api/v1/auth/roles"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _service(session: AsyncSession) -> RoleService:
    return RoleService(BaseRepository(session, Role))


def _bearer(user_id: int) -> dict[str, str]:
    token = create_access_token(user_id, extra_claims={"portal": "staff"})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def client(session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def _use_test_session() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_session] = _use_test_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Service-level tests
# ---------------------------------------------------------------------------


async def test_create_role_stamps_audit(session: AsyncSession) -> None:
    role = await _service(session).create(
        RoleCreate(name="Recruiter", description="Manages vacancies"), ACTOR
    )

    assert role.id is not None
    assert role.name == "Recruiter"
    assert role.is_active is True
    assert role.created_by == ACTOR.user_id


async def test_get_missing_role_raises(session: AsyncSession) -> None:
    with pytest.raises(RoleNotFoundError):
        await _service(session).get(999999)


async def test_update_role_changes_name(session: AsyncSession) -> None:
    service = _service(session)
    role = await service.create(RoleCreate(name="Temp"), ACTOR)

    updated = await service.update(role.id, RoleUpdate(name="Hiring Manager"), ACTOR)

    assert updated.name == "Hiring Manager"
    assert updated.updated_by == ACTOR.user_id


async def test_soft_delete_hides_role(session: AsyncSession) -> None:
    service = _service(session)
    role = await service.create(RoleCreate(name="Disposable"), ACTOR)

    await service.delete(role.id)

    with pytest.raises(RoleNotFoundError):
        await service.get(role.id)
    items, _ = await service.list(PageParams())
    assert role.id not in {r.id for r in items}


# ---------------------------------------------------------------------------
# Duplicate name guard
# ---------------------------------------------------------------------------


async def test_create_duplicate_name_raises(session: AsyncSession) -> None:
    service = _service(session)
    unique = f"Role-{uuid.uuid4().hex[:8]}"
    await service.create(RoleCreate(name=unique), ACTOR)

    with pytest.raises(RoleDuplicateError):
        await service.create(RoleCreate(name=unique), ACTOR)


async def test_update_duplicate_name_raises(session: AsyncSession) -> None:
    service = _service(session)
    name_a = f"RoleA-{uuid.uuid4().hex[:8]}"
    name_b = f"RoleB-{uuid.uuid4().hex[:8]}"
    await service.create(RoleCreate(name=name_a), ACTOR)
    role_b = await service.create(RoleCreate(name=name_b), ACTOR)

    with pytest.raises(RoleDuplicateError):
        await service.update(role_b.id, RoleUpdate(name=name_a), ACTOR)


# ---------------------------------------------------------------------------
# System role guards
# ---------------------------------------------------------------------------


async def test_delete_admin_role_raises_system_error(session: AsyncSession) -> None:
    result = await bootstrap_admin(session, f"{uuid.uuid4().hex[:12]}@test.local", "S3cret")
    with pytest.raises(SystemRoleError):
        await _service(session).delete(result.role_id)


async def test_update_system_role_raises_system_error(session: AsyncSession) -> None:
    result = await bootstrap_admin(session, f"{uuid.uuid4().hex[:12]}@test.local", "S3cret")
    with pytest.raises(SystemRoleError):
        await _service(session).update(result.role_id, RoleUpdate(name="Renamed"), ACTOR)


# ---------------------------------------------------------------------------
# Has-users delete guard
# ---------------------------------------------------------------------------


async def test_delete_role_with_users_raises(session: AsyncSession) -> None:
    """Deleting a role that has at least one active user assigned must be blocked."""
    bootstrap = await bootstrap_admin(session, f"{uuid.uuid4().hex[:12]}@test.local", "S3cret")
    service = _service(session)

    # Create a fresh role (not a system role)
    custom_role = await service.create(RoleCreate(name=f"Custom-{uuid.uuid4().hex[:8]}"), ACTOR)

    # Assign the admin user to this custom role as well
    session.add(
        UserRole(
            user_id=bootstrap.user_id,
            role_id=custom_role.id,
            created_by=ACTOR.user_id,
        )
    )
    await session.flush()

    with pytest.raises(RoleHasUsersError):
        await service.delete(custom_role.id)


async def test_delete_role_with_no_users_succeeds(session: AsyncSession) -> None:
    service = _service(session)
    empty_role = await service.create(RoleCreate(name=f"Empty-{uuid.uuid4().hex[:8]}"), ACTOR)
    # No users assigned — delete must succeed
    await service.delete(empty_role.id)
    with pytest.raises(RoleNotFoundError):
        await service.get(empty_role.id)


# ---------------------------------------------------------------------------
# HTTP endpoint tests
# ---------------------------------------------------------------------------


async def test_create_role_endpoint_201(
    client: AsyncClient, session: AsyncSession
) -> None:
    admin = await bootstrap_admin(session, f"{uuid.uuid4().hex[:12]}@test.local", "S3cret")
    name = f"HTTP-{uuid.uuid4().hex[:8]}"
    r = await client.post(
        ROLES_URL,
        json={"name": name, "description": "Test role"},
        headers=_bearer(admin.user_id),
    )
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == name
    assert body["is_active"] is True


async def test_create_role_endpoint_409_duplicate(
    client: AsyncClient, session: AsyncSession
) -> None:
    admin = await bootstrap_admin(session, f"{uuid.uuid4().hex[:12]}@test.local", "S3cret")
    name = f"Dup-{uuid.uuid4().hex[:8]}"
    payload = {"name": name}
    r1 = await client.post(ROLES_URL, json=payload, headers=_bearer(admin.user_id))
    assert r1.status_code == 201
    r2 = await client.post(ROLES_URL, json=payload, headers=_bearer(admin.user_id))
    assert r2.status_code == 409


async def test_update_role_endpoint_200(
    client: AsyncClient, session: AsyncSession
) -> None:
    admin = await bootstrap_admin(session, f"{uuid.uuid4().hex[:12]}@test.local", "S3cret")
    service = _service(session)
    role = await service.create(RoleCreate(name=f"ToEdit-{uuid.uuid4().hex[:8]}"), ACTOR)

    r = await client.patch(
        f"{ROLES_URL}/{role.id}",
        json={"name": f"Edited-{uuid.uuid4().hex[:8]}"},
        headers=_bearer(admin.user_id),
    )
    assert r.status_code == 200
    assert r.json()["name"].startswith("Edited-")


async def test_update_role_rejects_explicit_null_name(
    client: AsyncClient, session: AsyncSession
) -> None:
    """An explicit null name must be rejected (422), not written into the NOT NULL
    column where it would 500."""
    admin = await bootstrap_admin(session, f"{uuid.uuid4().hex[:12]}@test.local", "S3cret")
    service = _service(session)
    role = await service.create(RoleCreate(name=f"NullName-{uuid.uuid4().hex[:8]}"), ACTOR)

    r = await client.patch(
        f"{ROLES_URL}/{role.id}",
        json={"name": None},
        headers=_bearer(admin.user_id),
    )
    assert r.status_code == 422


async def test_update_system_role_endpoint_409(
    client: AsyncClient, session: AsyncSession
) -> None:
    admin = await bootstrap_admin(session, f"{uuid.uuid4().hex[:12]}@test.local", "S3cret")
    r = await client.patch(
        f"{ROLES_URL}/{admin.role_id}",
        json={"name": "HackedAdmin"},
        headers=_bearer(admin.user_id),
    )
    assert r.status_code == 409
    assert "system" in r.json()["detail"].lower()


async def test_delete_role_endpoint_204(
    client: AsyncClient, session: AsyncSession
) -> None:
    admin = await bootstrap_admin(session, f"{uuid.uuid4().hex[:12]}@test.local", "S3cret")
    service = _service(session)
    role = await service.create(RoleCreate(name=f"ToDel-{uuid.uuid4().hex[:8]}"), ACTOR)

    r = await client.delete(f"{ROLES_URL}/{role.id}", headers=_bearer(admin.user_id))
    assert r.status_code == 204


async def test_delete_system_role_endpoint_409(
    client: AsyncClient, session: AsyncSession
) -> None:
    admin = await bootstrap_admin(session, f"{uuid.uuid4().hex[:12]}@test.local", "S3cret")
    r = await client.delete(f"{ROLES_URL}/{admin.role_id}", headers=_bearer(admin.user_id))
    assert r.status_code == 409
    assert "system" in r.json()["detail"].lower()


async def test_delete_role_with_users_endpoint_409(
    client: AsyncClient, session: AsyncSession
) -> None:
    admin = await bootstrap_admin(session, f"{uuid.uuid4().hex[:12]}@test.local", "S3cret")
    service = _service(session)
    custom_role = await service.create(RoleCreate(name=f"Busy-{uuid.uuid4().hex[:8]}"), ACTOR)

    # Assign admin user to this new custom role
    session.add(UserRole(user_id=admin.user_id, role_id=custom_role.id, created_by=1))
    await session.flush()

    r = await client.delete(f"{ROLES_URL}/{custom_role.id}", headers=_bearer(admin.user_id))
    assert r.status_code == 409
    assert "user" in r.json()["detail"].lower()


async def test_create_role_endpoint_401_without_token(client: AsyncClient) -> None:
    r = await client.post(ROLES_URL, json={"name": "NoAuth"})
    assert r.status_code == 401


async def test_create_role_endpoint_403_without_permission(
    client: AsyncClient, session: AsyncSession
) -> None:
    """A user without auth.roles.create must receive 403."""
    from app.modules.org.infrastructure.parameters_repository import ParameterRepository
    from app.modules.auth.infrastructure.models import User

    portal = await ParameterRepository(session).get_by_type_and_code("user_portal", "staff")
    unprivileged = await BaseRepository(session, User).add(
        User(email=f"{uuid.uuid4().hex[:12]}@test.local", portal_id=portal.id)
    )

    r = await client.post(
        ROLES_URL,
        json={"name": "Unauthorized"},
        headers=_bearer(unprivileged.id),
    )
    assert r.status_code == 403
    assert "auth.roles.create" in r.json()["detail"]
