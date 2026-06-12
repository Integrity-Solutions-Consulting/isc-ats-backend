import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import CurrentUser
from app.modules.auth.application.role_permissions_service import (
    PermissionAlreadyGrantedError,
    PermissionGrantNotFoundError,
    PermissionNotFoundError,
    RoleNotFoundError,
    RolePermissionService,
)
from app.modules.auth.infrastructure.models import Permission, Role
from app.modules.auth.infrastructure.permissions_repository import PermissionRepository
from app.modules.auth.infrastructure.role_permissions_repository import (
    RolePermissionRepository,
)
from app.shared.repository import BaseRepository

ACTOR = CurrentUser(user_id=1, ip="127.0.0.1")


def _service(session: AsyncSession) -> RolePermissionService:
    return RolePermissionService(
        RolePermissionRepository(session),
        BaseRepository(session, Role),
        PermissionRepository(session),
    )


async def _make_role(session: AsyncSession) -> Role:
    return await BaseRepository(session, Role).add(Role(name=f"Role {uuid.uuid4().hex[:6]}"))


async def _make_permission(session: AsyncSession) -> Permission:
    return await PermissionRepository(session).add(
        Permission(code=f"test.{uuid.uuid4().hex[:12]}", name="Test permission")
    )


async def test_grant_permission_lists_it(session: AsyncSession) -> None:
    service = _service(session)
    role = await _make_role(session)
    permission = await _make_permission(session)

    granted = await service.grant(role.id, permission.id, ACTOR)
    permissions = await service.list_permissions(role.id)

    assert granted.id == permission.id
    assert {p.id for p in permissions} == {permission.id}


async def test_grant_unknown_role_raises(session: AsyncSession) -> None:
    permission = await _make_permission(session)
    with pytest.raises(RoleNotFoundError):
        await _service(session).grant(999999, permission.id, ACTOR)


async def test_grant_unknown_permission_raises(session: AsyncSession) -> None:
    role = await _make_role(session)
    with pytest.raises(PermissionNotFoundError):
        await _service(session).grant(role.id, 999999, ACTOR)


async def test_grant_twice_conflicts(session: AsyncSession) -> None:
    service = _service(session)
    role = await _make_role(session)
    permission = await _make_permission(session)
    await service.grant(role.id, permission.id, ACTOR)

    with pytest.raises(PermissionAlreadyGrantedError):
        await service.grant(role.id, permission.id, ACTOR)


async def test_revoke_then_regrant_reactivates(session: AsyncSession) -> None:
    service = _service(session)
    role = await _make_role(session)
    permission = await _make_permission(session)
    await service.grant(role.id, permission.id, ACTOR)

    await service.revoke(role.id, permission.id)
    assert await service.list_permissions(role.id) == []

    await service.grant(role.id, permission.id, ACTOR)
    assert {p.id for p in await service.list_permissions(role.id)} == {permission.id}


async def test_revoke_missing_grant_raises(session: AsyncSession) -> None:
    service = _service(session)
    role = await _make_role(session)
    permission = await _make_permission(session)
    with pytest.raises(PermissionGrantNotFoundError):
        await service.revoke(role.id, permission.id)
