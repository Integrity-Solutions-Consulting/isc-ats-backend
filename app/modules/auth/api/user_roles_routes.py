from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.dependencies import CurrentUserDep, SessionDep
from app.modules.auth.api.assignments_schemas import RoleAssignment
from app.modules.auth.api.authorization import require_permission
from app.modules.auth.api.roles_schemas import RoleRead
from app.modules.auth.application.user_roles_service import (
    RoleAlreadyAssignedError,
    RoleAssignmentNotFoundError,
    RoleNotFoundError,
    UserNotFoundError,
    UserRoleService,
)
from app.modules.auth.infrastructure.models import Role
from app.modules.auth.infrastructure.repository import UserRepository
from app.modules.auth.infrastructure.user_roles_repository import UserRoleRepository
from app.shared.repository import BaseRepository

router = APIRouter(prefix="/users", tags=["auth · user roles"])


def get_service(session: SessionDep) -> UserRoleService:
    return UserRoleService(
        UserRoleRepository(session),
        UserRepository(session),
        BaseRepository(session, Role),
    )


ServiceDep = Annotated[UserRoleService, Depends(get_service)]


@router.get(
    "/{user_id}/roles",
    response_model=list[RoleRead],
    dependencies=[Depends(require_permission("auth.user_roles.read"))],
)
async def list_user_roles(user_id: int, service: ServiceDep) -> list[RoleRead]:
    try:
        roles = await service.list_roles(user_id)
    except UserNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return [RoleRead.model_validate(r) for r in roles]


@router.post(
    "/{user_id}/roles",
    response_model=RoleRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("auth.user_roles.assign"))],
)
async def assign_user_role(
    user_id: int,
    data: RoleAssignment,
    service: ServiceDep,
    current_user: CurrentUserDep,
) -> RoleRead:
    try:
        role = await service.assign(user_id, data.role_id, current_user)
    except (UserNotFoundError, RoleNotFoundError) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except RoleAlreadyAssignedError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return RoleRead.model_validate(role)


@router.delete(
    "/{user_id}/roles/{role_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("auth.user_roles.revoke"))],
)
async def revoke_user_role(user_id: int, role_id: int, service: ServiceDep) -> None:
    try:
        await service.revoke(user_id, role_id)
    except RoleAssignmentNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.put(
    "/{user_id}/role",
    response_model=RoleRead,
    dependencies=[
        Depends(require_permission("auth.user_roles.assign")),
        Depends(require_permission("auth.user_roles.revoke")),
    ],
)
async def replace_user_role(
    user_id: int,
    data: RoleAssignment,
    service: ServiceDep,
    current_user: CurrentUserDep,
) -> RoleRead:
    """Replace a user's role entirely — the "editar rol" action from Usuarios.

    Revokes every role the user currently holds and assigns the new one, so
    their permissions and visible screens update immediately on next login/
    token refresh. Singular "/role" (vs the plural "/roles" collection above)
    signals single-role-per-user semantics, matching how the Usuarios screen
    only ever shows/edits one role per user.
    """
    try:
        role = await service.replace_role(user_id, data.role_id, current_user)
    except (UserNotFoundError, RoleNotFoundError) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return RoleRead.model_validate(role)
