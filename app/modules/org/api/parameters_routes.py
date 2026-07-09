from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.dependencies import CurrentUserDep, SessionDep
from app.modules.auth.api.authorization import PermissionCodesDep, require_permission
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
# parameters of type "vacancy_name" — every other catalog type is admin-managed.
# See ParameterService.create/update `restrict_to_types` (spec R8).
_ROLES_CREATE_PERMISSION = "auth.roles.create"
_RESTRICTED_PARAMETER_TYPES: set[str] = {"vacancy_name"}


def _restrict_to_types(caller_codes: set[str]) -> set[str] | None:
    if _ROLES_CREATE_PERMISSION in caller_codes:
        return None
    return _RESTRICTED_PARAMETER_TYPES


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
) -> ParameterRead:
    try:
        created = await service.create(
            data, current_user, restrict_to_types=_restrict_to_types(caller_codes)
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
) -> ParameterRead:
    try:
        updated = await service.update(
            parameter_id,
            data,
            current_user,
            restrict_to_types=_restrict_to_types(caller_codes),
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
async def delete_parameter(parameter_id: int, service: ServiceDep) -> None:
    try:
        await service.delete(parameter_id)
    except ParameterNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ParameterInUseError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
