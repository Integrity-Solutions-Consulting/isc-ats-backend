"""Per-role org.parameters TYPE allowlist (replaces the old hardcoded global
"non-admins may only write vacancy_name" rule).

Tests cover:
  1. grant_parameter_types_to_role idempotency (re-grant with a smaller set
     revokes the dropped types; re-granting the same set changes nothing extra)
  2. GET/PUT /auth/roles/{role_id}/parameter-types — 403 without permission,
     happy path with permission
  3. Talento Humano can create stage/stage_status parameters but still 403s on
     an out-of-allowlist type (department)
  4. Comercial/Proyecto still 403 on stage/stage_status (their allowlist is
     vacancy_name, city, career, work_mode, resource_level — the catalogs the
     vacancy-creation form actually reads from)

All async tests use a rolled-back session (unit-level) or the full ASGI app
(route-level). See test_internal_roles_slice5.py for the original R8 tests
(updated for the per-role model: TH no longer includes vacancy_name — that's
Comercial/Proyecto's catalog, not TH's — all three still exclude "title").
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.infrastructure.authorization_repository import (
    AuthorizationRepository,
)
from app.modules.auth.infrastructure.models import Role, RoleParameterTypeGrant

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


async def _staff_user_with_role(session: AsyncSession, role_name: str, *, tag: str) -> object:
    """Bootstrap the RBAC baseline and return a fresh User in the given internal role."""
    from app.modules.auth.application.bootstrap_service import bootstrap_admin  # noqa: PLC0415
    from app.modules.auth.infrastructure.models import User, UserRole  # noqa: PLC0415
    from app.modules.org.infrastructure.parameters_repository import (  # noqa: PLC0415
        ParameterRepository,
    )

    admin_result = await bootstrap_admin(
        session, f"admin-{uuid.uuid4().hex[:8]}@test.local", "S3cret"
    )

    params_repo = ParameterRepository(session)
    staff_portal = await params_repo.get_by_type_and_code("user_portal", "staff")
    assert staff_portal is not None

    role = (
        await session.execute(
            select(Role).where(Role.name == role_name).where(Role.is_active.is_(True))
        )
    ).scalar_one()

    user = User(
        email=f"{role_name.lower().replace(' ', '')}-{tag}@test.local",
        portal_id=staff_portal.id,
        email_verified=True,
        is_active=True,
    )
    session.add(user)
    await session.flush()
    session.add(UserRole(user_id=user.id, role_id=role.id, is_active=True))
    await session.flush()
    return user, admin_result


# ---------------------------------------------------------------------------
# 1 — grant_parameter_types_to_role idempotency
# ---------------------------------------------------------------------------


async def test_grant_parameter_types_to_role_upserts_active(session: AsyncSession) -> None:
    from app.modules.auth.application.bootstrap_service import (  # noqa: PLC0415
        grant_parameter_types_to_role,
    )

    role = Role(name=f"TestParamGrant-{uuid.uuid4().hex[:8]}", description="test")
    session.add(role)
    await session.flush()

    count = await grant_parameter_types_to_role(session, role.id, {"vacancy_name"})
    assert count == 1

    grant = (
        await session.execute(
            select(RoleParameterTypeGrant).where(
                RoleParameterTypeGrant.role_id == role.id,
                RoleParameterTypeGrant.parameter_type == "vacancy_name",
                RoleParameterTypeGrant.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    assert grant is not None


async def test_grant_parameter_types_to_role_revokes_dropped_types(
    session: AsyncSession,
) -> None:
    """Re-granting with a smaller set revokes the types dropped from the allowlist."""
    from app.modules.auth.application.bootstrap_service import (  # noqa: PLC0415
        grant_parameter_types_to_role,
    )

    role = Role(name=f"TestParamRevoke-{uuid.uuid4().hex[:8]}", description="test")
    session.add(role)
    await session.flush()

    await grant_parameter_types_to_role(session, role.id, {"vacancy_name", "stage"})
    await grant_parameter_types_to_role(session, role.id, {"vacancy_name"})

    stage_grant = (
        await session.execute(
            select(RoleParameterTypeGrant).where(
                RoleParameterTypeGrant.role_id == role.id,
                RoleParameterTypeGrant.parameter_type == "stage",
            )
        )
    ).scalar_one()
    assert stage_grant.is_active is False

    vacancy_grant = (
        await session.execute(
            select(RoleParameterTypeGrant).where(
                RoleParameterTypeGrant.role_id == role.id,
                RoleParameterTypeGrant.parameter_type == "vacancy_name",
            )
        )
    ).scalar_one()
    assert vacancy_grant.is_active is True


async def test_grant_parameter_types_to_role_idempotent_reapply(session: AsyncSession) -> None:
    """Granting the same set twice must not duplicate rows or change state."""
    from app.modules.auth.application.bootstrap_service import (  # noqa: PLC0415
        grant_parameter_types_to_role,
    )

    role = Role(name=f"TestParamIdempotent-{uuid.uuid4().hex[:8]}", description="test")
    session.add(role)
    await session.flush()

    await grant_parameter_types_to_role(session, role.id, {"vacancy_name"})
    await grant_parameter_types_to_role(session, role.id, {"vacancy_name"})

    grants = (
        await session.execute(
            select(RoleParameterTypeGrant).where(RoleParameterTypeGrant.role_id == role.id)
        )
    ).scalars().all()
    assert len(grants) == 1
    assert grants[0].is_active is True


async def test_list_parameter_types_for_user_reflects_role_grants(
    session: AsyncSession,
) -> None:
    user, _admin = await _staff_user_with_role(
        session, "Talento Humano", tag=uuid.uuid4().hex[:8]
    )
    types = await AuthorizationRepository(session).list_parameter_types_for_user(user.id)
    assert types == {"stage", "stage_status"}


async def test_list_parameter_types_for_user_comercial_is_vacancy_form_catalogs(
    session: AsyncSession,
) -> None:
    user, _admin = await _staff_user_with_role(session, "Comercial", tag=uuid.uuid4().hex[:8])
    types = await AuthorizationRepository(session).list_parameter_types_for_user(user.id)
    assert types == {"vacancy_name", "city", "career", "work_mode", "resource_level"}


# ---------------------------------------------------------------------------
# 2 — GET/PUT /auth/roles/{role_id}/parameter-types
# ---------------------------------------------------------------------------


async def test_get_role_parameter_types_forbidden_without_permission(
    session: AsyncSession,
) -> None:
    """Talento Humano (no auth.roles.read) reading a role's allowlist → 403."""
    from app.core.database import get_session  # noqa: PLC0415
    from app.core.security import create_access_token  # noqa: PLC0415
    from app.main import app  # noqa: PLC0415

    tag = uuid.uuid4().hex[:8]
    user, admin_result = await _staff_user_with_role(session, "Talento Humano", tag=tag)

    th_role = (
        await session.execute(
            select(Role).where(Role.name == "Talento Humano").where(Role.is_active.is_(True))
        )
    ).scalar_one()

    async def _use_test_session() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_session] = _use_test_session
    try:
        token = create_access_token(user.id, extra_claims={"portal": "staff"})
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.get(
                f"/api/v1/auth/roles/{th_role.id}/parameter-types",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert r.status_code == 403, f"Expected 403, got {r.status_code}: {r.text}"
    finally:
        app.dependency_overrides.clear()


async def test_get_role_parameter_types_happy_path(session: AsyncSession) -> None:
    """Admin (has auth.roles.read) reading TH's allowlist → 200 with the seeded types."""
    from app.core.database import get_session  # noqa: PLC0415
    from app.core.security import create_access_token  # noqa: PLC0415
    from app.main import app  # noqa: PLC0415
    from app.modules.auth.application.bootstrap_service import bootstrap_admin  # noqa: PLC0415

    tag = uuid.uuid4().hex[:8]
    admin_result = await bootstrap_admin(session, f"admin-{tag}@test.local", "S3cret")

    th_role = (
        await session.execute(
            select(Role).where(Role.name == "Talento Humano").where(Role.is_active.is_(True))
        )
    ).scalar_one()

    async def _use_test_session() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_session] = _use_test_session
    try:
        token = create_access_token(admin_result.user_id, extra_claims={"portal": "staff"})
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.get(
                f"/api/v1/auth/roles/{th_role.id}/parameter-types",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        body = r.json()
        assert set(body["parameter_types"]) == {"stage", "stage_status"}
    finally:
        app.dependency_overrides.clear()


async def test_put_role_parameter_types_forbidden_without_permission(
    session: AsyncSession,
) -> None:
    """Comercial (no auth.roles.update) replacing a role's allowlist → 403."""
    from app.core.database import get_session  # noqa: PLC0415
    from app.core.security import create_access_token  # noqa: PLC0415
    from app.main import app  # noqa: PLC0415

    tag = uuid.uuid4().hex[:8]
    user, _admin = await _staff_user_with_role(session, "Comercial", tag=tag)

    comercial_role = (
        await session.execute(
            select(Role).where(Role.name == "Comercial").where(Role.is_active.is_(True))
        )
    ).scalar_one()

    async def _use_test_session() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_session] = _use_test_session
    try:
        token = create_access_token(user.id, extra_claims={"portal": "staff"})
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.put(
                f"/api/v1/auth/roles/{comercial_role.id}/parameter-types",
                json={"parameter_types": ["stage"]},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert r.status_code == 403, f"Expected 403, got {r.status_code}: {r.text}"
    finally:
        app.dependency_overrides.clear()


async def test_put_role_parameter_types_happy_path_replaces_allowlist(
    session: AsyncSession,
) -> None:
    """Admin (has auth.roles.update) can grant Comercial the 'stage' type too."""
    from app.core.database import get_session  # noqa: PLC0415
    from app.core.security import create_access_token  # noqa: PLC0415
    from app.main import app  # noqa: PLC0415
    from app.modules.auth.application.bootstrap_service import bootstrap_admin  # noqa: PLC0415

    tag = uuid.uuid4().hex[:8]
    admin_result = await bootstrap_admin(session, f"admin-{tag}@test.local", "S3cret")

    comercial_role = (
        await session.execute(
            select(Role).where(Role.name == "Comercial").where(Role.is_active.is_(True))
        )
    ).scalar_one()

    async def _use_test_session() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_session] = _use_test_session
    try:
        token = create_access_token(admin_result.user_id, extra_claims={"portal": "staff"})
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.put(
                f"/api/v1/auth/roles/{comercial_role.id}/parameter-types",
                json={"parameter_types": ["vacancy_name", "stage"]},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        body = r.json()
        assert set(body["parameter_types"]) == {"vacancy_name", "stage"}
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 3 — Talento Humano can write stage/stage_status but not out-of-allowlist types
# ---------------------------------------------------------------------------


async def test_talento_humano_can_create_stage_parameter(session: AsyncSession) -> None:
    from app.core.database import get_session  # noqa: PLC0415
    from app.core.security import create_access_token  # noqa: PLC0415
    from app.main import app  # noqa: PLC0415

    tag = uuid.uuid4().hex[:8]
    user, _admin = await _staff_user_with_role(session, "Talento Humano", tag=tag)

    async def _use_test_session() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_session] = _use_test_session
    try:
        token = create_access_token(user.id, extra_claims={"portal": "staff"})
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post(
                "/api/v1/org/parameters",
                json={"type": "stage", "code": f"s-{tag}", "name": "Entrevista"},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert r.status_code == 201, f"Expected 201, got {r.status_code}: {r.text}"
    finally:
        app.dependency_overrides.clear()


async def test_talento_humano_can_create_stage_status_parameter(session: AsyncSession) -> None:
    from app.core.database import get_session  # noqa: PLC0415
    from app.core.security import create_access_token  # noqa: PLC0415
    from app.main import app  # noqa: PLC0415

    tag = uuid.uuid4().hex[:8]
    user, _admin = await _staff_user_with_role(session, "Talento Humano", tag=tag)

    async def _use_test_session() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_session] = _use_test_session
    try:
        token = create_access_token(user.id, extra_claims={"portal": "staff"})
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post(
                "/api/v1/org/parameters",
                json={"type": "stage_status", "code": f"ss-{tag}", "name": "Pendiente"},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert r.status_code == 201, f"Expected 201, got {r.status_code}: {r.text}"
    finally:
        app.dependency_overrides.clear()


async def test_talento_humano_forbidden_on_department_parameter(session: AsyncSession) -> None:
    """"department" is outside TH's allowlist (stage, stage_status)."""
    from app.core.database import get_session  # noqa: PLC0415
    from app.core.security import create_access_token  # noqa: PLC0415
    from app.main import app  # noqa: PLC0415

    tag = uuid.uuid4().hex[:8]
    user, _admin = await _staff_user_with_role(session, "Talento Humano", tag=tag)

    async def _use_test_session() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_session] = _use_test_session
    try:
        token = create_access_token(user.id, extra_claims={"portal": "staff"})
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post(
                "/api/v1/org/parameters",
                json={"type": "department", "code": f"d-{tag}", "name": "Ventas"},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert r.status_code == 403, f"Expected 403, got {r.status_code}: {r.text}"
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 4 — Comercial/Proyecto still 403 on stage/stage_status
# ---------------------------------------------------------------------------


async def test_comercial_forbidden_on_stage_parameter(session: AsyncSession) -> None:
    from app.core.database import get_session  # noqa: PLC0415
    from app.core.security import create_access_token  # noqa: PLC0415
    from app.main import app  # noqa: PLC0415

    tag = uuid.uuid4().hex[:8]
    user, _admin = await _staff_user_with_role(session, "Comercial", tag=tag)

    async def _use_test_session() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_session] = _use_test_session
    try:
        token = create_access_token(user.id, extra_claims={"portal": "staff"})
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post(
                "/api/v1/org/parameters",
                json={"type": "stage", "code": f"s-{tag}", "name": "Entrevista"},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert r.status_code == 403, f"Expected 403, got {r.status_code}: {r.text}"
    finally:
        app.dependency_overrides.clear()


async def test_proyecto_forbidden_on_stage_status_parameter(session: AsyncSession) -> None:
    from app.core.database import get_session  # noqa: PLC0415
    from app.core.security import create_access_token  # noqa: PLC0415
    from app.main import app  # noqa: PLC0415

    tag = uuid.uuid4().hex[:8]
    user, _admin = await _staff_user_with_role(session, "Proyecto", tag=tag)

    async def _use_test_session() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_session] = _use_test_session
    try:
        token = create_access_token(user.id, extra_claims={"portal": "staff"})
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post(
                "/api/v1/org/parameters",
                json={"type": "stage_status", "code": f"ss-{tag}", "name": "Pendiente"},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert r.status_code == 403, f"Expected 403, got {r.status_code}: {r.text}"
    finally:
        app.dependency_overrides.clear()
