from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.dependencies import CurrentUserDep, SessionDep
from app.modules.auth.api.authorization import require_permission
from app.modules.auth.api.permissions_schemas import (
    PermissionCreate,
    PermissionRead,
    PermissionUpdate,
)
from app.modules.auth.application.permissions_service import (
    DuplicatePermissionError,
    PermissionNotFoundError,
    PermissionService,
)
from app.modules.auth.infrastructure.permissions_repository import PermissionRepository
from app.shared.pagination import Page, PageParams

router = APIRouter(prefix="/permissions", tags=["auth · permissions"])


def get_service(session: SessionDep) -> PermissionService:
    return PermissionService(PermissionRepository(session))


ServiceDep = Annotated[PermissionService, Depends(get_service)]


@router.get(
    "",
    response_model=Page[PermissionRead],
    dependencies=[Depends(require_permission("auth.permissions.read"))],
)
async def list_permissions(
    service: ServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=300)] = 20,
    module: Annotated[str | None, Query(description="Filter by module")] = None,
) -> Page[PermissionRead]:
    params = PageParams(page=page, size=size)
    items, total = await service.list(params, module=module)
    return Page.create([PermissionRead.model_validate(i) for i in items], total, params)


@router.get(
    "/{permission_id}",
    response_model=PermissionRead,
    dependencies=[Depends(require_permission("auth.permissions.read"))],
)
async def get_permission(permission_id: int, service: ServiceDep) -> PermissionRead:
    try:
        return PermissionRead.model_validate(await service.get(permission_id))
    except PermissionNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post(
    "",
    response_model=PermissionRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("auth.permissions.create"))],
)
async def create_permission(
    data: PermissionCreate, service: ServiceDep, current_user: CurrentUserDep
) -> PermissionRead:
    try:
        created = await service.create(data, current_user)
    except DuplicatePermissionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return PermissionRead.model_validate(created)


@router.patch(
    "/{permission_id}",
    response_model=PermissionRead,
    dependencies=[Depends(require_permission("auth.permissions.update"))],
)
async def update_permission(
    permission_id: int,
    data: PermissionUpdate,
    service: ServiceDep,
    current_user: CurrentUserDep,
) -> PermissionRead:
    try:
        updated = await service.update(permission_id, data, current_user)
    except PermissionNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return PermissionRead.model_validate(updated)


@router.delete(
    "/{permission_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("auth.permissions.delete"))],
)
async def delete_permission(permission_id: int, service: ServiceDep) -> None:
    try:
        await service.delete(permission_id)
    except PermissionNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
