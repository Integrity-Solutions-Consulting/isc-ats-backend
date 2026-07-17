"""Slice 5 — Restrict parameter "type" creation/update by caller permission (spec R8).

Tasks:
  5.1  ParameterTypeForbiddenError is importable from parameters_service
  5.2  ParameterService.create raises ParameterTypeForbiddenError when
       restrict_to_types is set and data.type is outside it
  5.3  ParameterService.create allows the type when it is inside restrict_to_types
  5.4  ParameterService.create is unrestricted when restrict_to_types is None
  5.5  ParameterService.update raises ParameterTypeForbiddenError when the
       effective type (incoming data.type, else the existing parameter's type)
       is outside restrict_to_types
  5.6  ParameterService.update allows the effective type when it is inside
       restrict_to_types
  5.7  ParameterService.update is unrestricted when restrict_to_types is None
  5.8  create_parameter route: caller WITHOUT auth.roles.create is restricted to
       their role's org.parameters TYPE allowlist (auth.role_parameter_type_grants)
       — 403 for a type outside it, 201 for one inside it (e.g. "vacancy_name")
  5.9  create_parameter route: caller WITH auth.roles.create is unrestricted
  5.10 update_parameter route: caller WITHOUT auth.roles.create is restricted to
       their role's org.parameters TYPE allowlist — 403 for a type outside it,
       200 for one inside it (e.g. "vacancy_name")

All async tests use a rolled-back session (unit-level) or the full ASGI app
(route-level, spec R8). See test_parameter_type_grants.py for the per-role
allowlist model itself (grant_parameter_types_to_role, the GET/PUT
/auth/roles/{id}/parameter-types endpoints, and TH's extra stage/stage_status
grants).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.org.api.parameters_schemas import ParameterCreate, ParameterUpdate
from app.modules.org.application.parameters_service import (
    ParameterService,
    ParameterTypeForbiddenError,
)
from app.modules.org.infrastructure.models import Parameter
from app.modules.org.infrastructure.parameters_repository import ParameterRepository
from app.shared.repository import BaseRepository


def _service(session: AsyncSession) -> ParameterService:
    return ParameterService(ParameterRepository(session))


def _actor() -> object:
    """Minimal CurrentUser stand-in — service only reads .user_id / .ip."""
    from app.core.dependencies import CurrentUser  # noqa: PLC0415

    return CurrentUser(user_id=1, ip="127.0.0.1")


# ---------------------------------------------------------------------------
# 5.1 — ParameterTypeForbiddenError is importable
# ---------------------------------------------------------------------------


def test_parameter_type_forbidden_error_is_importable() -> None:
    from app.modules.org.application.parameters_service import (  # noqa: PLC0415
        ParameterError,
        ParameterTypeForbiddenError,
    )

    assert issubclass(ParameterTypeForbiddenError, ParameterError)


# ---------------------------------------------------------------------------
# 5.2 / 5.3 / 5.4 — ParameterService.create restriction
# ---------------------------------------------------------------------------


async def test_create_raises_when_type_outside_restrict_set(session: AsyncSession) -> None:
    service = _service(session)
    data = ParameterCreate(type="career", code=uuid.uuid4().hex[:8], name="Backend")
    with pytest.raises(ParameterTypeForbiddenError):
        await service.create(data, _actor(), restrict_to_types={"vacancy_name"})


async def test_create_allows_when_type_in_restrict_set(session: AsyncSession) -> None:
    service = _service(session)
    data = ParameterCreate(type="vacancy_name", code=uuid.uuid4().hex[:8], name="Dev")
    created = await service.create(data, _actor(), restrict_to_types={"vacancy_name"})
    assert created.type == "vacancy_name"


async def test_create_unrestricted_when_restrict_to_types_is_none(session: AsyncSession) -> None:
    service = _service(session)
    data = ParameterCreate(type="career", code=uuid.uuid4().hex[:8], name="Backend")
    created = await service.create(data, _actor(), restrict_to_types=None)
    assert created.type == "career"


# ---------------------------------------------------------------------------
# 5.5 / 5.6 / 5.7 — ParameterService.update restriction
# ---------------------------------------------------------------------------


async def test_update_raises_when_current_type_outside_restrict_set(
    session: AsyncSession,
) -> None:
    param = await BaseRepository(session, Parameter).add(
        Parameter(type="career", code=uuid.uuid4().hex[:8], name="Backend")
    )
    service = _service(session)
    with pytest.raises(ParameterTypeForbiddenError):
        await service.update(
            param.id,
            ParameterUpdate(name="Backend Dev"),
            _actor(),
            restrict_to_types={"vacancy_name"},
        )


async def test_update_allows_when_current_type_in_restrict_set(session: AsyncSession) -> None:
    param = await BaseRepository(session, Parameter).add(
        Parameter(type="vacancy_name", code=uuid.uuid4().hex[:8], name="Dev")
    )
    service = _service(session)
    updated = await service.update(
        param.id,
        ParameterUpdate(name="Senior Dev"),
        _actor(),
        restrict_to_types={"vacancy_name"},
    )
    assert updated.name == "Senior Dev"


async def test_update_raises_when_changing_type_outside_restrict_set(
    session: AsyncSession,
) -> None:
    """Even a restricted-type parameter cannot be re-typed to a forbidden type."""
    param = await BaseRepository(session, Parameter).add(
        Parameter(type="vacancy_name", code=uuid.uuid4().hex[:8], name="Dev")
    )
    service = _service(session)
    with pytest.raises(ParameterTypeForbiddenError):
        await service.update(
            param.id,
            ParameterUpdate(type="career"),
            _actor(),
            restrict_to_types={"vacancy_name"},
        )


async def test_update_unrestricted_when_restrict_to_types_is_none(session: AsyncSession) -> None:
    param = await BaseRepository(session, Parameter).add(
        Parameter(type="career", code=uuid.uuid4().hex[:8], name="Backend")
    )
    service = _service(session)
    updated = await service.update(
        param.id,
        ParameterUpdate(name="Backend Dev"),
        _actor(),
        restrict_to_types=None,
    )
    assert updated.name == "Backend Dev"


# ---------------------------------------------------------------------------
# Route-level helpers
# ---------------------------------------------------------------------------


async def _staff_user_with_role(
    session: AsyncSession, role_name: str, *, tag: str
) -> object:
    """Bootstrap the RBAC baseline and return a fresh User in the given internal role."""
    from app.modules.auth.application.bootstrap_service import bootstrap_admin  # noqa: PLC0415
    from app.modules.auth.infrastructure.models import Role, User, UserRole  # noqa: PLC0415

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
# 5.8 / 5.9 — create_parameter route
# ---------------------------------------------------------------------------


async def test_create_parameter_route_forbids_restricted_caller_off_allowlist(
    session: AsyncSession,
) -> None:
    """Talento Humano (allowlist: stage, stage_status) creating
    type='career' → 403 (career is outside TH's allowlist)."""
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
                json={"type": "career", "code": f"c-{tag}", "name": "Backend"},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert r.status_code == 403, f"Expected 403, got {r.status_code}: {r.text}"
    finally:
        app.dependency_overrides.clear()


async def test_create_parameter_route_allows_restricted_caller_vacancy_name(
    session: AsyncSession,
) -> None:
    """Comercial (allowlist: vacancy_name) creating type='vacancy_name' → 201."""
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
                json={"type": "vacancy_name", "code": f"vn-{tag}", "name": "Dev"},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert r.status_code == 201, f"Expected 201, got {r.status_code}: {r.text}"
    finally:
        app.dependency_overrides.clear()


async def test_create_parameter_route_unrestricted_for_caller_with_roles_create(
    session: AsyncSession,
) -> None:
    """Admin (has auth.roles.create) creating type='career' → 201 (unrestricted)."""
    from app.core.database import get_session  # noqa: PLC0415
    from app.core.security import create_access_token  # noqa: PLC0415
    from app.main import app  # noqa: PLC0415
    from app.modules.auth.application.bootstrap_service import bootstrap_admin  # noqa: PLC0415

    tag = uuid.uuid4().hex[:8]
    admin_result = await bootstrap_admin(session, f"admin-{tag}@test.local", "S3cret")

    async def _use_test_session() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_session] = _use_test_session
    try:
        token = create_access_token(admin_result.user_id, extra_claims={"portal": "staff"})
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post(
                "/api/v1/org/parameters",
                json={"type": "career", "code": f"c-{tag}", "name": "Backend"},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert r.status_code == 201, f"Expected 201, got {r.status_code}: {r.text}"
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 5.10 — update_parameter route
# ---------------------------------------------------------------------------


async def test_update_parameter_route_forbids_restricted_caller_off_allowlist(
    session: AsyncSession,
) -> None:
    """Proyecto (allowlist: vacancy_name, city, career, work_mode, resource_level)
    updating a 'title' parameter → 403 (title is outside Proyecto's allowlist —
    it's not one of the vacancy-creation-form catalogs)."""
    from app.core.database import get_session  # noqa: PLC0415
    from app.core.security import create_access_token  # noqa: PLC0415
    from app.main import app  # noqa: PLC0415

    tag = uuid.uuid4().hex[:8]
    user, _admin = await _staff_user_with_role(session, "Proyecto", tag=tag)
    param = await BaseRepository(session, Parameter).add(
        Parameter(type="title", code=f"tt-{tag}", name="Backend")
    )

    async def _use_test_session() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_session] = _use_test_session
    try:
        token = create_access_token(user.id, extra_claims={"portal": "staff"})
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.patch(
                f"/api/v1/org/parameters/{param.id}",
                json={"name": "Backend Dev"},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert r.status_code == 403, f"Expected 403, got {r.status_code}: {r.text}"
    finally:
        app.dependency_overrides.clear()


async def test_update_parameter_route_allows_restricted_caller_vacancy_name(
    session: AsyncSession,
) -> None:
    """Comercial (allowlist: vacancy_name) updating a 'vacancy_name'
    parameter → 200."""
    from app.core.database import get_session  # noqa: PLC0415
    from app.core.security import create_access_token  # noqa: PLC0415
    from app.main import app  # noqa: PLC0415

    tag = uuid.uuid4().hex[:8]
    user, _admin = await _staff_user_with_role(session, "Comercial", tag=tag)
    param = await BaseRepository(session, Parameter).add(
        Parameter(type="vacancy_name", code=f"vn-{tag}", name="Dev")
    )

    async def _use_test_session() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_session] = _use_test_session
    try:
        token = create_access_token(user.id, extra_claims={"portal": "staff"})
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.patch(
                f"/api/v1/org/parameters/{param.id}",
                json={"name": "Senior Dev"},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    finally:
        app.dependency_overrides.clear()


async def test_create_parameter_route_allows_comercial_on_vacancy_form_catalog(
    session: AsyncSession,
) -> None:
    """Comercial creating type='city' → 201 (city is one of the vacancy-creation-
    form catalogs Comercial/Proyecto self-manage, alongside vacancy_name)."""
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
                json={"type": "city", "code": f"ct-{tag}", "name": "Quito"},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert r.status_code == 201, f"Expected 201, got {r.status_code}: {r.text}"
    finally:
        app.dependency_overrides.clear()


async def test_delete_parameter_route_allows_comercial_on_allowlisted_type(
    session: AsyncSession,
) -> None:
    """Comercial deleting a 'vacancy_name' parameter → 204 (org.parameters.delete
    was missing from COMERCIAL_PERMISSION_CODES; full CRUD requires it too)."""
    from app.core.database import get_session  # noqa: PLC0415
    from app.core.security import create_access_token  # noqa: PLC0415
    from app.main import app  # noqa: PLC0415

    tag = uuid.uuid4().hex[:8]
    user, _admin = await _staff_user_with_role(session, "Comercial", tag=tag)
    param = await BaseRepository(session, Parameter).add(
        Parameter(type="vacancy_name", code=f"vn-{tag}", name="Dev")
    )

    async def _use_test_session() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_session] = _use_test_session
    try:
        token = create_access_token(user.id, extra_claims={"portal": "staff"})
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.delete(
                f"/api/v1/org/parameters/{param.id}",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert r.status_code == 204, f"Expected 204, got {r.status_code}: {r.text}"
    finally:
        app.dependency_overrides.clear()


async def test_delete_parameter_route_forbids_comercial_off_allowlist(
    session: AsyncSession,
) -> None:
    """Comercial deleting a 'title' parameter → 403 (title is outside their
    allowlist even though they now hold org.parameters.delete)."""
    from app.core.database import get_session  # noqa: PLC0415
    from app.core.security import create_access_token  # noqa: PLC0415
    from app.main import app  # noqa: PLC0415

    tag = uuid.uuid4().hex[:8]
    user, _admin = await _staff_user_with_role(session, "Comercial", tag=tag)
    param = await BaseRepository(session, Parameter).add(
        Parameter(type="title", code=f"tt-{tag}", name="Dev")
    )

    async def _use_test_session() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_session] = _use_test_session
    try:
        token = create_access_token(user.id, extra_claims={"portal": "staff"})
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.delete(
                f"/api/v1/org/parameters/{param.id}",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert r.status_code == 403, f"Expected 403, got {r.status_code}: {r.text}"
    finally:
        app.dependency_overrides.clear()
