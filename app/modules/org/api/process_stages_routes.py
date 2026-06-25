from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.dependencies import CurrentUserDep, SessionDep
from app.modules.auth.api.authorization import require_permission
from app.modules.org.api.process_stages_schemas import (
    ProcessStageCreate,
    ProcessStageRead,
    ProcessStageUpdate,
)
from app.modules.org.application.process_stages_service import (
    DuplicateStageError,
    ProcessStageInUseError,
    ProcessStageNotFoundError,
    ProcessStageProtectedError,
    ProcessStageReferenceError,
    ProcessStageService,
)
from app.modules.org.infrastructure.models import Parameter, Process
from app.modules.org.infrastructure.process_stages_repository import (
    ProcessStageRepository,
)
from app.modules.recruitment.infrastructure.application_usage_repository import (
    ApplicationUsageRepository,
)
from app.shared.repository import BaseRepository

router = APIRouter(prefix="/process-stages", tags=["org · process stages"])


def get_service(session: SessionDep) -> ProcessStageService:
    applications = ApplicationUsageRepository(session)
    return ProcessStageService(
        ProcessStageRepository(session),
        BaseRepository(session, Process),
        BaseRepository(session, Parameter),
        in_use_checker=applications.has_active_in_stage,
    )


ServiceDep = Annotated[ProcessStageService, Depends(get_service)]


@router.get(
    "",
    response_model=list[ProcessStageRead],
    dependencies=[Depends(require_permission("org.process_stages.read"))],
)
async def list_process_stages(
    service: ServiceDep,
    process_id: Annotated[int, Query(description="Process whose stages to list")],
) -> list[ProcessStageRead]:
    stages = await service.list_by_process(process_id)
    return [ProcessStageRead.model_validate(s) for s in stages]


@router.get(
    "/{process_stage_id}",
    response_model=ProcessStageRead,
    dependencies=[Depends(require_permission("org.process_stages.read"))],
)
async def get_process_stage(
    process_stage_id: int, service: ServiceDep
) -> ProcessStageRead:
    try:
        return ProcessStageRead.model_validate(await service.get(process_stage_id))
    except ProcessStageNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post(
    "",
    response_model=ProcessStageRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("org.process_stages.create"))],
)
async def create_process_stage(
    data: ProcessStageCreate, service: ServiceDep, current_user: CurrentUserDep
) -> ProcessStageRead:
    try:
        created = await service.create(data, current_user)
    except ProcessStageReferenceError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except DuplicateStageError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return ProcessStageRead.model_validate(created)


@router.patch(
    "/{process_stage_id}",
    response_model=ProcessStageRead,
    dependencies=[Depends(require_permission("org.process_stages.update"))],
)
async def update_process_stage(
    process_stage_id: int,
    data: ProcessStageUpdate,
    service: ServiceDep,
    current_user: CurrentUserDep,
) -> ProcessStageRead:
    try:
        updated = await service.update(process_stage_id, data, current_user)
    except ProcessStageNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ProcessStageProtectedError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ProcessStageReferenceError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except DuplicateStageError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return ProcessStageRead.model_validate(updated)


@router.delete(
    "/{process_stage_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("org.process_stages.delete"))],
)
async def delete_process_stage(process_stage_id: int, service: ServiceDep) -> None:
    try:
        await service.delete(process_stage_id)
    except ProcessStageNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ProcessStageProtectedError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ProcessStageInUseError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
