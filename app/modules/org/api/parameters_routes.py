from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.dependencies import CurrentUserDep, SessionDep
from app.modules.auth.api.authorization import PermissionCodesDep, require_permission
from app.modules.auth.infrastructure.authorization_repository import (
    AuthorizationRepository,
)
from app.modules.org.api.parameters_schemas import (
    ParameterCreate,
    ParameterRead,
    ParameterUpdate,
)
from app.modules.org.application.parameters_service import (
    DuplicateParameterError,
    ParameterInUseError,
    ParameterNotFoundError,
    ParameterService,
    ParameterTypeForbiddenError,
)
from app.modules.org.infrastructure.parameter_usage_repository import (
    ParameterUsageRepository,
)
from app.modules.org.infrastructure.parameters_repository import ParameterRepository
from app.shared.pagination import Page, PageParams

router = APIRouter(prefix="/parameters", tags=["org · parameters"])

# Callers without auth.roles.create (i.e. not full admins) may only create/update
# parameters whose type is in their role's writable-type allowlist
# (auth.role_parameter_type_grants) — every other catalog type is forbidden.
# See ParameterService.create/update `restrict_to_types` (spec R8).
_ROLES_CREATE_PERMISSION = "auth.roles.create"


async def _restrict_to_types(
    caller_codes: set[str], current_user: CurrentUserDep, session: SessionDep
) -> set[str] | None:
    """The set of org.parameters TYPE values `current_user` may create/update.

    Returns None when the caller is unrestricted (holds auth.roles.create — full
    admin). Otherwise returns the caller's role-granted allowlist, which may be
    an EMPTY set — that is fail-closed (write access to zero catalog types), and
    is distinct from the None/"unrestricted" case.
    """
    if _ROLES_CREATE_PERMISSION in caller_codes:
        return None
    return await AuthorizationRepository(session).list_parameter_types_for_user(
        current_user.user_id
    )


def get_service(session: SessionDep) -> ParameterService:
    usage = ParameterUsageRepository(session)
    return ParameterService(
        ParameterRepository(session), in_use_checker=usage.is_referenced
    )


ServiceDep = Annotated[ParameterService, Depends(get_service)]


@router.get(
    "",
    response_model=Page[ParameterRead],
    dependencies=[Depends(require_permission("org.parameters.read"))],
)
async def list_parameters(
    service: ServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    type: Annotated[str | None, Query(description="Filter by catalog type")] = None,
    include_inactive: Annotated[bool, Query()] = False,
) -> Page[ParameterRead]:
    params = PageParams(page=page, size=size)
    items, total = await service.list(params, type_=type, include_inactive=include_inactive)
    return Page.create([ParameterRead.model_validate(i) for i in items], total, params)


@router.get(
    "/{parameter_id}",
    response_model=ParameterRead,
    dependencies=[Depends(require_permission("org.parameters.read"))],
)
async def get_parameter(parameter_id: int, service: ServiceDep) -> ParameterRead:
    try:
        return ParameterRead.model_validate(await service.get(parameter_id))
    except ParameterNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post(
    "",
    response_model=ParameterRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("org.parameters.create"))],
)
async def create_parameter(
    data: ParameterCreate,
    service: ServiceDep,
    current_user: CurrentUserDep,
    caller_codes: PermissionCodesDep,
    session: SessionDep,
) -> ParameterRead:
    try:
        created = await service.create(
            data,
            current_user,
            restrict_to_types=await _restrict_to_types(caller_codes, current_user, session),
        )
    except DuplicateParameterError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ParameterTypeForbiddenError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    return ParameterRead.model_validate(created)


@router.patch(
    "/{parameter_id}",
    response_model=ParameterRead,
    dependencies=[Depends(require_permission("org.parameters.update"))],
)
async def update_parameter(
    parameter_id: int,
    data: ParameterUpdate,
    service: ServiceDep,
    current_user: CurrentUserDep,
    caller_codes: PermissionCodesDep,
    session: SessionDep,
) -> ParameterRead:
    try:
        updated = await service.update(
            parameter_id,
            data,
            current_user,
            restrict_to_types=await _restrict_to_types(caller_codes, current_user, session),
        )
    except ParameterNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ParameterTypeForbiddenError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    return ParameterRead.model_validate(updated)


@router.delete(
    "/{parameter_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("org.parameters.delete"))],
)
async def delete_parameter(
    parameter_id: int,
    service: ServiceDep,
    current_user: CurrentUserDep,
    caller_codes: PermissionCodesDep,
    session: SessionDep,
) -> None:
    try:
        await service.delete(
            parameter_id,
            restrict_to_types=await _restrict_to_types(caller_codes, current_user, session),
        )
    except ParameterNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ParameterTypeForbiddenError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except ParameterInUseError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
