from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.dependencies import CurrentUserDep, SessionDep
from app.modules.auth.api.authorization import require_permission
from app.modules.auth.infrastructure.models import User
from app.modules.recruitment.api.interviewer_availability_schemas import (
    AvailabilityCreate,
    AvailabilityRead,
    AvailabilityUpdate,
)
from app.modules.recruitment.application.interviewer_availability_service import (
    AvailabilityNotFoundError,
    AvailabilityReferenceError,
    AvailabilityValidationError,
    InterviewerAvailabilityService,
)
from app.modules.recruitment.infrastructure.interview_models import (
    InterviewerAvailability,
)
from app.shared.pagination import Page, PageParams
from app.shared.repository import BaseRepository

router = APIRouter(
    prefix="/interviewer-availability",
    tags=["recruitment · interviewer availability"],
)


def get_service(session: SessionDep) -> InterviewerAvailabilityService:
    return InterviewerAvailabilityService(
        BaseRepository(session, InterviewerAvailability),
        BaseRepository(session, User),
    )


ServiceDep = Annotated[InterviewerAvailabilityService, Depends(get_service)]
_READ = Depends(require_permission("recruitment.interviewer_availability.read"))


@router.get("", response_model=Page[AvailabilityRead], dependencies=[_READ])
async def list_availability(
    service: ServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    user_id: Annotated[int | None, Query()] = None,
) -> Page[AvailabilityRead]:
    params = PageParams(page=page, size=size)
    items, total = await service.list(params, user_id=user_id)
    return Page.create([AvailabilityRead.model_validate(i) for i in items], total, params)


@router.get("/{availability_id}", response_model=AvailabilityRead, dependencies=[_READ])
async def get_availability(
    availability_id: int, service: ServiceDep
) -> AvailabilityRead:
    try:
        return AvailabilityRead.model_validate(await service.get(availability_id))
    except AvailabilityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post(
    "",
    response_model=AvailabilityRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("recruitment.interviewer_availability.create"))],
)
async def create_availability(
    data: AvailabilityCreate, service: ServiceDep, current_user: CurrentUserDep
) -> AvailabilityRead:
    try:
        created = await service.create(data, current_user)
    except AvailabilityReferenceError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except AvailabilityValidationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return AvailabilityRead.model_validate(created)


@router.patch(
    "/{availability_id}",
    response_model=AvailabilityRead,
    dependencies=[Depends(require_permission("recruitment.interviewer_availability.update"))],
)
async def update_availability(
    availability_id: int,
    data: AvailabilityUpdate,
    service: ServiceDep,
    current_user: CurrentUserDep,
) -> AvailabilityRead:
    try:
        updated = await service.update(availability_id, data, current_user)
    except AvailabilityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except AvailabilityValidationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return AvailabilityRead.model_validate(updated)


@router.delete(
    "/{availability_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("recruitment.interviewer_availability.delete"))],
)
async def delete_availability(availability_id: int, service: ServiceDep) -> None:
    try:
        await service.delete(availability_id)
    except AvailabilityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
