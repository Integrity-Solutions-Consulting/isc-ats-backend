from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.dependencies import CurrentUserDep, SessionDep
from app.modules.auth.api.authorization import require_permission
from app.modules.org.api.processes_schemas import (
    ProcessCreate,
    ProcessRead,
    ProcessUpdate,
)
from app.modules.org.application.processes_service import (
    DuplicateProcessError,
    ProcessNotFoundError,
    ProcessReferenceError,
    ProcessService,
)
from app.modules.org.infrastructure.models import ClientCompany, Department
from app.modules.org.infrastructure.processes_repository import ProcessRepository
from app.shared.pagination import Page, PageParams
from app.shared.repository import BaseRepository

router = APIRouter(prefix="/processes", tags=["org · processes"])


def get_service(session: SessionDep) -> ProcessService:
    return ProcessService(
        ProcessRepository(session),
        BaseRepository(session, ClientCompany),
        BaseRepository(session, Department),
    )


ServiceDep = Annotated[ProcessService, Depends(get_service)]


@router.get(
    "",
    response_model=Page[ProcessRead],
    dependencies=[Depends(require_permission("org.processes.read"))],
)
async def list_processes(
    service: ServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    client_company_id: Annotated[int | None, Query()] = None,
    department_id: Annotated[int | None, Query()] = None,
    include_inactive: Annotated[bool, Query()] = False,
) -> Page[ProcessRead]:
    params = PageParams(page=page, size=size)
    items, total = await service.list(
        params, client_company_id=client_company_id, department_id=department_id,
        include_inactive=include_inactive,
    )
    return Page.create([ProcessRead.model_validate(i) for i in items], total, params)


@router.get(
    "/{process_id}",
    response_model=ProcessRead,
    dependencies=[Depends(require_permission("org.processes.read"))],
)
async def get_process(process_id: int, service: ServiceDep) -> ProcessRead:
    try:
        return ProcessRead.model_validate(await service.get(process_id))
    except ProcessNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post(
    "",
    response_model=ProcessRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("org.processes.create"))],
)
async def create_process(
    data: ProcessCreate, service: ServiceDep, current_user: CurrentUserDep
) -> ProcessRead:
    try:
        created = await service.create(data, current_user)
    except ProcessReferenceError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except DuplicateProcessError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return ProcessRead.model_validate(created)


@router.patch(
    "/{process_id}",
    response_model=ProcessRead,
    dependencies=[Depends(require_permission("org.processes.update"))],
)
async def update_process(
    process_id: int,
    data: ProcessUpdate,
    service: ServiceDep,
    current_user: CurrentUserDep,
) -> ProcessRead:
    try:
        updated = await service.update(process_id, data, current_user)
    except ProcessNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ProcessReferenceError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except DuplicateProcessError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return ProcessRead.model_validate(updated)


@router.delete(
    "/{process_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("org.processes.delete"))],
)
async def delete_process(process_id: int, service: ServiceDep) -> None:
    try:
        await service.delete(process_id)
    except ProcessNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
