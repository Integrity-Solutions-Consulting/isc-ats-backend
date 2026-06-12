from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.dependencies import CurrentUserDep, SessionDep
from app.modules.auth.api.authorization import require_permission
from app.modules.auth.infrastructure.models import User
from app.modules.org.infrastructure.models import Parameter, ProcessStage
from app.modules.recruitment.api.interviews_schemas import (
    InterviewCreate,
    InterviewRead,
    InterviewUpdate,
)
from app.modules.recruitment.application.interviews_service import (
    InterviewNotFoundError,
    InterviewReferenceError,
    InterviewService,
    InterviewValidationError,
)
from app.modules.recruitment.infrastructure.application_models import Application
from app.modules.recruitment.infrastructure.interview_models import Interview
from app.shared.pagination import Page, PageParams
from app.shared.repository import BaseRepository

router = APIRouter(prefix="/interviews", tags=["recruitment · interviews"])


def get_service(session: SessionDep) -> InterviewService:
    return InterviewService(
        BaseRepository(session, Interview),
        BaseRepository(session, Application),
        BaseRepository(session, ProcessStage),
        BaseRepository(session, User),
        BaseRepository(session, Parameter),
    )


ServiceDep = Annotated[InterviewService, Depends(get_service)]
_READ = Depends(require_permission("recruitment.interviews.read"))


@router.get("", response_model=Page[InterviewRead], dependencies=[_READ])
async def list_interviews(
    service: ServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    application_id: Annotated[int | None, Query()] = None,
) -> Page[InterviewRead]:
    params = PageParams(page=page, size=size)
    items, total = await service.list(params, application_id=application_id)
    return Page.create([InterviewRead.model_validate(i) for i in items], total, params)


@router.get("/{interview_id}", response_model=InterviewRead, dependencies=[_READ])
async def get_interview(interview_id: int, service: ServiceDep) -> InterviewRead:
    try:
        return InterviewRead.model_validate(await service.get(interview_id))
    except InterviewNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post(
    "",
    response_model=InterviewRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("recruitment.interviews.create"))],
)
async def create_interview(
    data: InterviewCreate, service: ServiceDep, current_user: CurrentUserDep
) -> InterviewRead:
    try:
        created = await service.create(data, current_user)
    except InterviewReferenceError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except InterviewValidationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return InterviewRead.model_validate(created)


@router.patch(
    "/{interview_id}",
    response_model=InterviewRead,
    dependencies=[Depends(require_permission("recruitment.interviews.update"))],
)
async def update_interview(
    interview_id: int,
    data: InterviewUpdate,
    service: ServiceDep,
    current_user: CurrentUserDep,
) -> InterviewRead:
    try:
        updated = await service.update(interview_id, data, current_user)
    except InterviewNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except InterviewReferenceError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except InterviewValidationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return InterviewRead.model_validate(updated)


@router.delete(
    "/{interview_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("recruitment.interviews.delete"))],
)
async def delete_interview(interview_id: int, service: ServiceDep) -> None:
    try:
        await service.delete(interview_id)
    except InterviewNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
