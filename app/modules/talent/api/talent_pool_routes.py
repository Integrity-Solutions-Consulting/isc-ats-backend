from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.dependencies import CurrentUserDep, SessionDep
from app.modules.auth.api.authorization import require_permission
from app.modules.recruitment.api.candidates_schemas import CandidateDirectoryRead
from app.modules.recruitment.infrastructure.candidate_models import Candidate
from app.modules.recruitment.infrastructure.candidates_expanded import (
    CandidatesExpandedRepository,
)
from app.modules.recruitment.infrastructure.models import Vacancy
from app.modules.talent.api.talent_pool_schemas import TalentPoolCreate, TalentPoolRead
from app.modules.talent.application.talent_pool_service import (
    DuplicateTalentPoolError,
    TalentPoolNotFoundError,
    TalentPoolReferenceError,
    TalentPoolService,
)
from app.modules.talent.infrastructure.models import TalentPool
from app.shared.ownership import forbid_candidate_portal
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
    "/candidates",
    response_model=Page[CandidateDirectoryRead],
    dependencies=[Depends(require_permission("recruitment.candidates.read"))],
)
async def list_all_candidates_directory(
    session: SessionDep,
    current_user: CurrentUserDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    search: Annotated[
        str | None,
        Query(description="Búsqueda por nombre, apellido, correo o cédula del candidato"),
    ] = None,
    skill: Annotated[
        str | None,
        Query(description="Filtrar por conocimientos o tecnologías (ej: Python, C#)"),
    ] = None,
    tool: Annotated[
        str | None,
        Query(description="Filtrar por herramientas o software (ej: Docker, Power BI)"),
    ] = None,
    career: Annotated[
        str | None,
        Query(description="Filtrar por carrera profesional"),
    ] = None,
    career_id: Annotated[int | None, Query(description="Filtrar por ID de carrera")] = None,
    city_id: Annotated[int | None, Query(description="Filtrar por ID de ciudad")] = None,
    vacancy_id: Annotated[
        int | None, Query(description="Filtrar por ID de vacante postulada")
    ] = None,
    in_talent_pool: Annotated[
        bool | None,
        Query(description="Filtrar si el candidato pertenece o no al banco de talento"),
    ] = None,
    only_applied: Annotated[
        bool,
        Query(description="Filtrar únicamente candidatos que tengan al menos una postulación"),
    ] = False,
    include_inactive: Annotated[bool, Query()] = False,
) -> Page[CandidateDirectoryRead]:
    """Query all candidate profiles (registered applicants) with enriched
    information matching the talent pool view."""
    forbid_candidate_portal(current_user)
    params = PageParams(page=page, size=size)
    items, total = await CandidatesExpandedRepository(session).list_directory(
        params,
        search=search,
        skill=skill,
        tool=tool,
        career=career,
        career_id=career_id,
        city_id=city_id,
        vacancy_id=vacancy_id,
        in_talent_pool=in_talent_pool,
        only_applied=only_applied,
        include_inactive=include_inactive,
    )
    return Page.create(items, total, params)


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
    skill: Annotated[
        str | None,
        Query(description="Filtrar por conocimientos o tecnologías (ej: Python, C#)"),
    ] = None,
    tool: Annotated[
        str | None,
        Query(description="Filtrar por herramientas o software (ej: Docker, Power BI)"),
    ] = None,
    search: Annotated[
        str | None,
        Query(description="Búsqueda por nombre, apellido o correo del candidato"),
    ] = None,
    include_inactive: Annotated[bool, Query()] = False,
) -> Page[TalentPoolRead]:
    params = PageParams(page=page, size=size)
    items, total = await service.list(
        params,
        candidate_id=candidate_id,
        source_vacancy_id=source_vacancy_id,
        skill=skill,
        tool=tool,
        search=search,
        include_inactive=include_inactive,
    )
    return Page.create(items, total, params)


@router.get(
    "/{entry_id}",
    response_model=TalentPoolRead,
    dependencies=[Depends(require_permission("talent.talent_pool.read"))],
)
async def get_talent_pool_entry(entry_id: int, service: ServiceDep) -> TalentPoolRead:
    try:
        return await service.get_enriched(entry_id)
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
    except DuplicateTalentPoolError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return await service.get_enriched(created.id)


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