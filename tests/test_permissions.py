import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import CurrentUser
from app.modules.auth.api.permissions_schemas import PermissionCreate
from app.modules.auth.application.permissions_service import (
    DuplicatePermissionError,
    PermissionService,
)
from app.modules.auth.infrastructure.permissions_repository import PermissionRepository

ACTOR = CurrentUser(user_id=1, ip="127.0.0.1")


def _service(session: AsyncSession) -> PermissionService:
    return PermissionService(PermissionRepository(session))


def _unique_code() -> str:
    return f"test.{uuid.uuid4().hex[:12]}"


async def test_create_permission_stamps_audit(session: AsyncSession) -> None:
    perm = await _service(session).create(
        PermissionCreate(code=_unique_code(), name="Create departments", module="org"),
        ACTOR,
    )

    assert perm.id is not None
    assert perm.module == "org"
    assert perm.is_active is True
    assert perm.created_by == ACTOR.user_id


async def test_create_permission_rejects_duplicate_code(session: AsyncSession) -> None:
    service = _service(session)
    code = _unique_code()
    await service.create(PermissionCreate(code=code, name="First"), ACTOR)

    with pytest.raises(DuplicatePermissionError):
        await service.create(PermissionCreate(code=code, name="Second"), ACTOR)
