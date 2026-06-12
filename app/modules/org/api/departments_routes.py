from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.dependencies import CurrentUserDep, SessionDep
from app.modules.auth.api.authorization import require_permission
from app.modules.org.api.departments_schemas import (
    DepartmentCreate,
    DepartmentRead,
    DepartmentUpdate,
)
from app.modules.org.application.departments_service import (
    DepartmentNotFoundError,
    DepartmentService,
)
from app.modules.org.infrastructure.models import Department
from app.shared.pagination import Page, PageParams
from app.shared.repository import BaseRepository

router = APIRouter(prefix="/departments", tags=["org · departments"])


def get_service(session: SessionDep) -> DepartmentService:
    return DepartmentService(BaseRepository(session, Department))


ServiceDep = Annotated[DepartmentService, Depends(get_service)]


@router.get(
    "",
    response_model=Page[DepartmentRead],
    dependencies=[Depends(require_permission("org.departments.read"))],
)
async def list_departments(
    service: ServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    include_inactive: Annotated[bool, Query()] = False,
) -> Page[DepartmentRead]:
    params = PageParams(page=page, size=size)
    items, total = await service.list(params, include_inactive=include_inactive)
    return Page.create([DepartmentRead.model_validate(i) for i in items], total, params)


@router.get(
    "/{department_id}",
    response_model=DepartmentRead,
    dependencies=[Depends(require_permission("org.departments.read"))],
)
async def get_department(department_id: int, service: ServiceDep) -> DepartmentRead:
    try:
        return DepartmentRead.model_validate(await service.get(department_id))
    except DepartmentNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post(
    "",
    response_model=DepartmentRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("org.departments.create"))],
)
async def create_department(
    data: DepartmentCreate, service: ServiceDep, current_user: CurrentUserDep
) -> DepartmentRead:
    return DepartmentRead.model_validate(await service.create(data, current_user))


@router.patch(
    "/{department_id}",
    response_model=DepartmentRead,
    dependencies=[Depends(require_permission("org.departments.update"))],
)
async def update_department(
    department_id: int,
    data: DepartmentUpdate,
    service: ServiceDep,
    current_user: CurrentUserDep,
) -> DepartmentRead:
    try:
        updated = await service.update(department_id, data, current_user)
    except DepartmentNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return DepartmentRead.model_validate(updated)


@router.delete(
    "/{department_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("org.departments.delete"))],
)
async def delete_department(department_id: int, service: ServiceDep) -> None:
    try:
        await service.delete(department_id)
    except DepartmentNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
