from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.dependencies import CurrentUserDep, SessionDep
from app.modules.auth.api.authorization import require_permission
from app.modules.org.api.parameters_schemas import (
    ParameterCreate,
    ParameterRead,
    ParameterUpdate,
)
from app.modules.org.application.parameters_service import (
    DuplicateParameterError,
    ParameterNotFoundError,
    ParameterService,
)
from app.modules.org.infrastructure.parameters_repository import ParameterRepository
from app.shared.pagination import Page, PageParams

router = APIRouter(prefix="/parameters", tags=["org · parameters"])


def get_service(session: SessionDep) -> ParameterService:
    return ParameterService(ParameterRepository(session))


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
) -> ParameterRead:
    try:
        created = await service.create(data, current_user)
    except DuplicateParameterError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
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
) -> ParameterRead:
    try:
        updated = await service.update(parameter_id, data, current_user)
    except ParameterNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
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
