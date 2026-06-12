import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import CurrentUser
from app.modules.auth.application.user_roles_service import (
    RoleAlreadyAssignedError,
    RoleAssignmentNotFoundError,
    RoleNotFoundError,
    UserNotFoundError,
    UserRoleService,
)
from app.modules.auth.infrastructure.models import Role, User
from app.modules.auth.infrastructure.repository import UserRepository
from app.modules.auth.infrastructure.user_roles_repository import UserRoleRepository
from app.modules.org.infrastructure.parameters_repository import ParameterRepository
from app.shared.repository import BaseRepository

ACTOR = CurrentUser(user_id=1, ip="127.0.0.1")


def _service(session: AsyncSession) -> UserRoleService:
    return UserRoleService(
        UserRoleRepository(session),
        UserRepository(session),
        BaseRepository(session, Role),
    )


async def _make_user(session: AsyncSession) -> User:
    # Resolve a real portal parameter (seeded staff|candidate) — portal_id is a FK.
    portal = await ParameterRepository(session).get_by_type_and_code("user_portal", "staff")
    assert portal is not None, "user_portal:staff parameter must be seeded"
    return await UserRepository(session).add(
        User(email=f"{uuid.uuid4().hex[:12]}@test.local", portal_id=portal.id)
    )


async def _make_role(session: AsyncSession) -> Role:
    return await BaseRepository(session, Role).add(Role(name=f"Role {uuid.uuid4().hex[:6]}"))


async def test_assign_role_lists_it(session: AsyncSession) -> None:
    service = _service(session)
    user = await _make_user(session)
    role = await _make_role(session)

    assigned = await service.assign(user.id, role.id, ACTOR)
    roles = await service.list_roles(user.id)

    assert assigned.id == role.id
    assert {r.id for r in roles} == {role.id}


async def test_assign_unknown_user_raises(session: AsyncSession) -> None:
    role = await _make_role(session)
    with pytest.raises(UserNotFoundError):
        await _service(session).assign(999999, role.id, ACTOR)


async def test_assign_unknown_role_raises(session: AsyncSession) -> None:
    user = await _make_user(session)
    with pytest.raises(RoleNotFoundError):
        await _service(session).assign(user.id, 999999, ACTOR)


async def test_assign_twice_conflicts(session: AsyncSession) -> None:
    service = _service(session)
    user = await _make_user(session)
    role = await _make_role(session)
    await service.assign(user.id, role.id, ACTOR)

    with pytest.raises(RoleAlreadyAssignedError):
        await service.assign(user.id, role.id, ACTOR)


async def test_revoke_then_reassign_reactivates(session: AsyncSession) -> None:
    service = _service(session)
    user = await _make_user(session)
    role = await _make_role(session)
    await service.assign(user.id, role.id, ACTOR)

    await service.revoke(user.id, role.id)
    assert await service.list_roles(user.id) == []

    # Re-assigning must reactivate the existing row, not insert a duplicate PK.
    await service.assign(user.id, role.id, ACTOR)
    assert {r.id for r in await service.list_roles(user.id)} == {role.id}


async def test_revoke_missing_assignment_raises(session: AsyncSession) -> None:
    service = _service(session)
    user = await _make_user(session)
    role = await _make_role(session)
    with pytest.raises(RoleAssignmentNotFoundError):
        await service.revoke(user.id, role.id)
