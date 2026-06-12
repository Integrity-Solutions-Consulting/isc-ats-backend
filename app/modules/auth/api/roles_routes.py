from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.dependencies import CurrentUserDep, SessionDep
from app.modules.auth.api.authorization import require_permission
from app.modules.auth.api.roles_schemas import RoleCreate, RoleRead, RoleUpdate
from app.modules.auth.application.roles_service import (
    RoleDuplicateError,
    RoleHasUsersError,
    RoleNotFoundError,
    RoleService,
    SystemRoleError,
)
from app.modules.auth.infrastructure.models import Role
from app.shared.pagination import Page, PageParams
from app.shared.repository import BaseRepository

router = APIRouter(prefix="/roles", tags=["auth · roles"])


def get_service(session: SessionDep) -> RoleService:
    return RoleService(BaseRepository(session, Role))


ServiceDep = Annotated[RoleService, Depends(get_service)]


@router.get(
    "",
    response_model=Page[RoleRead],
    dependencies=[Depends(require_permission("auth.roles.read"))],
)
async def list_roles(
    service: ServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[RoleRead]:
    params = PageParams(page=page, size=size)
    items, total = await service.list(params)
    return Page.create([RoleRead.model_validate(i) for i in items], total, params)


@router.get(
    "/{role_id}",
    response_model=RoleRead,
    dependencies=[Depends(require_permission("auth.roles.read"))],
)
async def get_role(role_id: int, service: ServiceDep) -> RoleRead:
    try:
        return RoleRead.model_validate(await service.get(role_id))
    except RoleNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post(
    "",
    response_model=RoleRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("auth.roles.create"))],
)
async def create_role(
    data: RoleCreate, service: ServiceDep, current_user: CurrentUserDep
) -> RoleRead:
    try:
        return RoleRead.model_validate(await service.create(data, current_user))
    except RoleDuplicateError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.patch(
    "/{role_id}",
    response_model=RoleRead,
    dependencies=[Depends(require_permission("auth.roles.update"))],
)
async def update_role(
    role_id: int,
    data: RoleUpdate,
    service: ServiceDep,
    current_user: CurrentUserDep,
) -> RoleRead:
    try:
        updated = await service.update(role_id, data, current_user)
    except RoleNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except RoleDuplicateError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except SystemRoleError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return RoleRead.model_validate(updated)


@router.delete(
    "/{role_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("auth.roles.delete"))],
)
async def delete_role(role_id: int, service: ServiceDep) -> None:
    try:
        await service.delete(role_id)
    except RoleNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except SystemRoleError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except RoleHasUsersError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
