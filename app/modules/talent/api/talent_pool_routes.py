from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.dependencies import CurrentUserDep, SessionDep
from app.modules.auth.api.authorization import require_permission
from app.modules.recruitment.infrastructure.candidate_models import Candidate
from app.modules.recruitment.infrastructure.models import Vacancy
from app.modules.talent.api.talent_pool_schemas import TalentPoolCreate, TalentPoolRead
from app.modules.talent.application.talent_pool_service import (
    TalentPoolNotFoundError,
    TalentPoolReferenceError,
    TalentPoolService,
)
from app.modules.talent.infrastructure.models import TalentPool
from app.shared.pagination import Page, PageParams
from app.shared.repository import BaseRepository

router = APIRouter(prefix="/talent-pool", tags=["talent · talent pool"])


def get_service(session: SessionDep) -> TalentPoolService:
    return TalentPoolService(
        BaseRepository(session, TalentPool),
        BaseRepository(session, Candidate),
        BaseRepository(session, Vacancy),
    )


ServiceDep = Annotated[TalentPoolService, Depends(get_service)]


@router.get(
    "",
    response_model=Page[TalentPoolRead],
    dependencies=[Depends(require_permission("talent.talent_pool.read"))],
)
async def list_talent_pool(
    service: ServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    candidate_id: Annotated[int | None, Query()] = None,
    source_vacancy_id: Annotated[int | None, Query()] = None,
) -> Page[TalentPoolRead]:
    params = PageParams(page=page, size=size)
    items, total = await service.list(
        params,
        candidate_id=candidate_id,
        source_vacancy_id=source_vacancy_id,
    )
    return Page.create([TalentPoolRead.model_validate(i) for i in items], total, params)


@router.get(
    "/{entry_id}",
    response_model=TalentPoolRead,
    dependencies=[Depends(require_permission("talent.talent_pool.read"))],
)
async def get_talent_pool_entry(entry_id: int, service: ServiceDep) -> TalentPoolRead:
    try:
        return TalentPoolRead.model_validate(await service.get(entry_id))
    except TalentPoolNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post(
    "",
    response_model=TalentPoolRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("talent.talent_pool.create"))],
)
async def add_to_talent_pool(
    data: TalentPoolCreate, service: ServiceDep, current_user: CurrentUserDep
) -> TalentPoolRead:
    try:
        created = await service.create(data, current_user)
    except TalentPoolReferenceError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return TalentPoolRead.model_validate(created)


@router.delete(
    "/{entry_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("talent.talent_pool.delete"))],
)
async def remove_from_talent_pool(entry_id: int, service: ServiceDep) -> None:
    try:
        await service.delete(entry_id)
    except TalentPoolNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
