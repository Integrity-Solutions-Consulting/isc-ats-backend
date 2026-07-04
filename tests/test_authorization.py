import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import CurrentUser
from app.modules.auth.api.authorization import require_permission
from app.modules.auth.application.role_permissions_service import RolePermissionService
from app.modules.auth.application.user_roles_service import UserRoleService
from app.modules.auth.infrastructure.authorization_repository import (
    AuthorizationRepository,
)
from app.modules.auth.infrastructure.models import Permission, Role, User
from app.modules.auth.infrastructure.permissions_repository import PermissionRepository
from app.modules.auth.infrastructure.repository import UserRepository
from app.modules.auth.infrastructure.role_permissions_repository import (
    RolePermissionRepository,
)
from app.modules.auth.infrastructure.user_roles_repository import UserRoleRepository
from app.modules.org.infrastructure.parameters_repository import ParameterRepository
from app.shared.repository import BaseRepository

ACTOR = CurrentUser(user_id=1, ip="127.0.0.1")


async def _wire_user_with_permission(
    session: AsyncSession, code: str
) -> User:
    """Create user + role + permission and link them all (the happy path)."""
    portal = await ParameterRepository(session).get_by_type_and_code("user_portal", "staff")
    assert portal is not None
    user = await UserRepository(session).add(
        User(email=f"{uuid.uuid4().hex[:12]}@test.local", portal_id=portal.id)
    )
    role = await BaseRepository(session, Role).add(Role(name=f"Role {uuid.uuid4().hex[:6]}"))
    permission = await PermissionRepository(session).add(
        Permission(code=code, name="Test permission")
    )

    user_roles = UserRoleService(
        UserRoleRepository(session), UserRepository(session), BaseRepository(session, Role)
    )
    role_perms = RolePermissionService(
        RolePermissionRepository(session),
        BaseRepository(session, Role),
        PermissionRepository(session),
    )
    await user_roles.assign(user.id, role.id, ACTOR)
    await role_perms.grant(role.id, permission.id, ACTOR)
    return user


async def test_permission_codes_resolve_through_the_chain(session: AsyncSession) -> None:
    code = f"test.{uuid.uuid4().hex[:12]}"
    user = await _wire_user_with_permission(session, code)

    codes = await AuthorizationRepository(session).list_permission_codes_for_user(user.id)

    assert code in codes


async def test_revoking_role_drops_the_permission(session: AsyncSession) -> None:
    code = f"test.{uuid.uuid4().hex[:12]}"
    user = await _wire_user_with_permission(session, code)
    repo = AuthorizationRepository(session)

    # Revoke every role the user has; the permission must disappear.
    for role in await UserRoleRepository(session).list_roles_for_user(user.id):
        await UserRoleService(
            UserRoleRepository(session),
            UserRepository(session),
            BaseRepository(session, Role),
        ).revoke(user.id, role.id)

    assert await repo.list_permission_codes_for_user(user.id) == set()


async def test_inactive_role_drops_the_permission(session: AsyncSession) -> None:
    """A role marked is_active=False must confer no permissions, even while the
    user_role link is still active."""
    code = f"test.{uuid.uuid4().hex[:12]}"
    user = await _wire_user_with_permission(session, code)
    repo = AuthorizationRepository(session)
    assert code in await repo.list_permission_codes_for_user(user.id)

    # Deactivate the role directly (the user_role link stays active).
    roles = await UserRoleRepository(session).list_roles_for_user(user.id)
    assert roles
    for role in roles:
        role.is_active = False
    await session.flush()

    assert await repo.list_permission_codes_for_user(user.id) == set()
    assert await repo.list_permission_ids_for_user(user.id) == set()


async def test_user_without_roles_has_no_permissions(session: AsyncSession) -> None:
    portal = await ParameterRepository(session).get_by_type_and_code("user_portal", "staff")
    assert portal is not None
    user = await UserRepository(session).add(
        User(email=f"{uuid.uuid4().hex[:12]}@test.local", portal_id=portal.id)
    )

    codes = await AuthorizationRepository(session).list_permission_codes_for_user(user.id)

    assert codes == set()


async def test_require_permission_allows_when_present() -> None:
    checker = require_permission("org.departments.create")
    principal = CurrentUser(user_id=7, ip=None)

    result = await checker(
        codes={"org.departments.create", "org.departments.read"},
        current_user=principal,
    )

    assert result is principal


async def test_require_permission_forbids_when_absent() -> None:
    checker = require_permission("org.departments.create")
    principal = CurrentUser(user_id=7, ip=None)

    with pytest.raises(HTTPException) as exc:
        await checker(codes={"org.departments.read"}, current_user=principal)

    assert exc.value.status_code == 403


async def test_require_permission_forbids_candidate_without_permission() -> None:
    """Candidate-portal users must be subject to the same permission check.

    This test fails before the fix because of the portal=="candidate" bypass
    on lines 42-43 of authorization.py.
    """
    checker = require_permission("auth.roles.create")
    # Candidate user who has NO permission for the admin-only code.
    principal = CurrentUser(user_id=99, ip=None, portal="candidate")

    with pytest.raises(HTTPException) as exc:
        await checker(codes=set(), current_user=principal)

    assert exc.value.status_code == 403
