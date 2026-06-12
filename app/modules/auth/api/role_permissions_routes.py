from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.dependencies import CurrentUserDep, SessionDep
from app.modules.auth.api.assignments_schemas import PermissionGrant
from app.modules.auth.api.authorization import require_permission
from app.modules.auth.api.permissions_schemas import PermissionRead
from app.modules.auth.application.role_permissions_service import (
    PermissionAlreadyGrantedError,
    PermissionGrantNotFoundError,
    PermissionNotFoundError,
    RoleNotFoundError,
    RolePermissionService,
)
from app.modules.auth.infrastructure.models import Role
from app.modules.auth.infrastructure.permissions_repository import PermissionRepository
from app.modules.auth.infrastructure.role_permissions_repository import (
    RolePermissionRepository,
)
from app.shared.repository import BaseRepository

router = APIRouter(prefix="/roles", tags=["auth · role permissions"])


def get_service(session: SessionDep) -> RolePermissionService:
    return RolePermissionService(
        RolePermissionRepository(session),
        BaseRepository(session, Role),
        PermissionRepository(session),
    )


ServiceDep = Annotated[RolePermissionService, Depends(get_service)]


@router.get(
    "/{role_id}/permissions",
    response_model=list[PermissionRead],
    dependencies=[Depends(require_permission("auth.role_permissions.read"))],
)
async def list_role_permissions(
    role_id: int, service: ServiceDep
) -> list[PermissionRead]:
    try:
        permissions = await service.list_permissions(role_id)
    except RoleNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return [PermissionRead.model_validate(p) for p in permissions]


@router.post(
    "/{role_id}/permissions",
    response_model=PermissionRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("auth.role_permissions.grant"))],
)
async def grant_role_permission(
    role_id: int,
    data: PermissionGrant,
    service: ServiceDep,
    current_user: CurrentUserDep,
) -> PermissionRead:
    try:
        permission = await service.grant(role_id, data.permission_id, current_user)
    except (RoleNotFoundError, PermissionNotFoundError) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except PermissionAlreadyGrantedError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return PermissionRead.model_validate(permission)


@router.delete(
    "/{role_id}/permissions/{permission_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("auth.role_permissions.revoke"))],
)
async def revoke_role_permission(
    role_id: int, permission_id: int, service: ServiceDep
) -> None:
    try:
        await service.revoke(role_id, permission_id)
    except PermissionGrantNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
