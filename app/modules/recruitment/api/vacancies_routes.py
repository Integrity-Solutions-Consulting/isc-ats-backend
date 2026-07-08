import io
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from app.core.dependencies import CurrentUserDep, SessionDep
from app.modules.auth.api.authorization import require_any_permission, require_permission
from app.modules.org.infrastructure.models import (
    ClientCompany,
    Contact,
    Department,
    Parameter,
    Process,
    ProfileTemplate,
)
from app.modules.recruitment.api.application_notes_schemas import _author_name_from_email
from app.modules.recruitment.api.vacancies_schemas import (
    PipelineCardSchema,
    PipelineSchema,
    PipelineStageSchema,
    PublicVacancyItem,
    VacancyCreate,
    VacancyDocumentItem,
    VacancyListItem,
    VacancyRead,
    VacancyStageItem,
    VacancyUpdate,
)
from app.modules.recruitment.application.poster_generator_service import generate_vacancy_poster
from app.modules.recruitment.application.vacancies_service import (
    VacancyCloseError,
    VacancyInUseError,
    VacancyNotFoundError,
    VacancyReferenceError,
    VacancyService,
)
from app.modules.recruitment.infrastructure.application_usage_repository import (
    ApplicationUsageRepository,
)
from app.modules.recruitment.infrastructure.models import Vacancy
from app.modules.recruitment.infrastructure.pipeline_repository import (
    PipelineRepository,
)
from app.modules.recruitment.infrastructure.vacancies_repository import (
    VacanciesExpandedRepository,
)
from app.shared.ownership import forbid_candidate_portal
from app.shared.pagination import Page, PageParams
from app.shared.repository import BaseRepository

router = APIRouter(prefix="/vacancies", tags=["recruitment · vacancies"])


# ── Public endpoints (no authentication required) ─────────────────────────────

@router.get(
    "/public",
    response_model=Page[PublicVacancyItem],
    summary="List active vacancies — public, no auth required",
)
async def list_vacancies_public(
    session: SessionDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 100,
) -> Page[PublicVacancyItem]:
    """Returns active vacancies with public-safe fields only (client company omitted)."""
    repo = VacanciesExpandedRepository(session)
    params = PageParams(page=page, size=size)
    items, total = await repo.list_expanded(
        params,
        status_code="active",
        include_inactive=False,
    )
    public_items = [
        PublicVacancyItem(
            id=v.id,
            vacancy_name=v.vacancy_name,
            career=v.career,
            city=v.city,
            work_mode=v.work_mode,
            resource_level=v.resource_level,
            openings=v.openings,
            experience_years=v.experience_years,
            work_schedule=v.work_schedule,
            project_duration_years=v.project_duration_years,
            project_duration_months=v.project_duration_months,
            description=v.description,
            profile_requirements=v.profile_requirements,
            created_at=v.created_at,
        )
        for v in items
    ]
    return Page.create(public_items, total, params)


@router.get(
    "/public/{vacancy_id}",
    response_model=PublicVacancyItem,
    summary="Get a single active vacancy — public, no auth required",
)
async def get_vacancy_public(vacancy_id: int, session: SessionDep) -> PublicVacancyItem:
    """Returns a single vacancy with public-safe fields only (client company omitted)."""
    repo = VacanciesExpandedRepository(session)
    item = await repo.get_expanded(vacancy_id)
    if item is None or not item.is_active or item.vacancy_status != "active":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Vacancy not found")
    return PublicVacancyItem(
        id=item.id,
        vacancy_name=item.vacancy_name,
        career=item.career,
        city=item.city,
        work_mode=item.work_mode,
        resource_level=item.resource_level,
        openings=item.openings,
        experience_years=item.experience_years,
        work_schedule=item.work_schedule,
        project_duration_years=item.project_duration_years,
        project_duration_months=item.project_duration_months,
        description=item.description,
        profile_requirements=item.profile_requirements,
        created_at=item.created_at,
    )


# ── Authenticated endpoints ────────────────────────────────────────────────────

def get_service(session: SessionDep) -> VacancyService:
    applications = ApplicationUsageRepository(session)
    return VacancyService(
        BaseRepository(session, Vacancy),
        BaseRepository(session, Parameter),
        BaseRepository(session, ClientCompany),
        BaseRepository(session, Contact),
        BaseRepository(session, Department),
        BaseRepository(session, Process),
        BaseRepository(session, ProfileTemplate),
        PipelineRepository(session),
        applications_checker=applications.has_active_for_vacancy,
    )


ServiceDep = Annotated[VacancyService, Depends(get_service)]


@router.get(
    "/expanded",
    response_model=Page[VacancyListItem],
    dependencies=[Depends(require_permission("recruitment.vacancies.read"))],
)
async def list_vacancies_expanded(
    session: SessionDep,
    current_user: CurrentUserDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 50,
    client_company_id: Annotated[int | None, Query()] = None,
    status_id: Annotated[int | None, Query()] = None,
    department_id: Annotated[int | None, Query()] = None,
    include_inactive: Annotated[bool, Query()] = False,
) -> Page[VacancyListItem]:
    forbid_candidate_portal(current_user)
    repo = VacanciesExpandedRepository(session)
    params = PageParams(page=page, size=size)
    items, total = await repo.list_expanded(
        params,
        client_company_id=client_company_id,
        status_id=status_id,
        department_id=department_id,
        include_inactive=include_inactive,
    )
    return Page.create(
        [VacancyListItem(**vars(item)) for item in items], total, params
    )


@router.get(
    "",
    response_model=Page[VacancyRead],
    dependencies=[Depends(require_permission("recruitment.vacancies.read"))],
)
async def list_vacancies(
    service: ServiceDep,
    current_user: CurrentUserDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    client_company_id: Annotated[int | None, Query()] = None,
    status_id: Annotated[int | None, Query()] = None,
    department_id: Annotated[int | None, Query()] = None,
) -> Page[VacancyRead]:
    forbid_candidate_portal(current_user)
    params = PageParams(page=page, size=size)
    items, total = await service.list(
        params,
        client_company_id=client_company_id,
        status_id=status_id,
        department_id=department_id,
    )
    return Page.create([VacancyRead.model_validate(i) for i in items], total, params)


@router.get(
    "/{vacancy_id}",
    response_model=VacancyRead,
    dependencies=[Depends(require_permission("recruitment.vacancies.read"))],
)
async def get_vacancy(
    vacancy_id: int, service: ServiceDep, current_user: CurrentUserDep
) -> VacancyRead:
    forbid_candidate_portal(current_user)
    try:
        return VacancyRead.model_validate(await service.get(vacancy_id))
    except VacancyNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post(
    "",
    response_model=VacancyRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("recruitment.vacancies.create"))],
)
async def create_vacancy(
    data: VacancyCreate, service: ServiceDep, current_user: CurrentUserDep
) -> VacancyRead:
    try:
        created = await service.create(data, current_user)
    except VacancyReferenceError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return VacancyRead.model_validate(created)


@router.patch(
    "/{vacancy_id}",
    response_model=VacancyRead,
    dependencies=[Depends(require_permission("recruitment.vacancies.update"))],
)
async def update_vacancy(
    vacancy_id: int,
    data: VacancyUpdate,
    service: ServiceDep,
    current_user: CurrentUserDep,
) -> VacancyRead:
    try:
        updated = await service.update(vacancy_id, data, current_user)
    except VacancyNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except VacancyReferenceError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except VacancyCloseError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return VacancyRead.model_validate(updated)


@router.delete(
    "/{vacancy_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("recruitment.vacancies.delete"))],
)
async def delete_vacancy(vacancy_id: int, service: ServiceDep) -> None:
    try:
        await service.delete(vacancy_id)
    except VacancyNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except VacancyInUseError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.get(
    "/{vacancy_id}/stages",
    response_model=list[VacancyStageItem],
    dependencies=[
        Depends(
            require_any_permission(
                "recruitment.vacancies.read",
                "recruitment.vacancies.read_stages",
            )
        )
    ],
    summary="List process stages for a vacancy — accessible by candidates",
)
async def get_vacancy_stages(vacancy_id: int, session: SessionDep) -> list[VacancyStageItem]:
    """Returns the ordered process stages of a vacancy's pipeline.

    Used by the candidate portal to render real stage names and progress.
    Omits client and contact data — safe for candidate-portal tokens.
    """
    data = await PipelineRepository(session).get_pipeline(vacancy_id)
    # Expose a sequential 1..N display position, not the stored sort key: the
    # final stage is seeded at a reserved high order (9999) so it always sorts
    # last, and that internal value must never surface in the candidate portal.
    return [
        VacancyStageItem(
            id=s.id,
            name=s.name,
            order=position,
            is_final_positive=s.is_final_positive,
            is_initial=s.is_initial,
        )
        for position, s in enumerate(data.stages, start=1)
    ]


@router.get(
    "/{vacancy_id}/generate-poster",
    dependencies=[Depends(require_permission("recruitment.vacancies.read"))],
)
async def generate_poster(
    vacancy_id: int, current_user: CurrentUserDep
) -> StreamingResponse:
    forbid_candidate_portal(current_user)
    try:
        image_bytes = await generate_vacancy_poster(vacancy_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return StreamingResponse(
        io.BytesIO(image_bytes),
        media_type="image/png",
        headers={"Content-Disposition": f'attachment; filename="poster_{vacancy_id}.png"'},
    )


@router.get(
    "/{vacancy_id}/documents",
    response_model=list[VacancyDocumentItem],
    dependencies=[Depends(require_permission("recruitment.vacancies.read"))],
    summary="List generated Word profile documents for all candidates in a vacancy",
)
async def get_vacancy_documents(
    vacancy_id: int, session: SessionDep, current_user: CurrentUserDep
) -> list[VacancyDocumentItem]:
    """Returns all application_documents records linked to this vacancy's applications,
    enriched with candidate name, stage name at generation, and author display name.
    """
    forbid_candidate_portal(current_user)
    rows = await PipelineRepository(session).get_vacancy_documents(vacancy_id)

    avatar_colors = [
        "bg-primary-600", "bg-accent-500", "bg-primary-400",
        "bg-primary-700", "bg-accent-600", "bg-primary-300",
        "bg-accent-400", "bg-primary-500",
    ]

    # Build version map per candidate (count docs ascending by date → version number)
    version_map: dict[int, int] = {}
    for row in reversed(rows):  # oldest first to assign version numbers
        version_map[row.candidate_id] = version_map.get(row.candidate_id, 0) + 1

    # Re-iterate in original (desc) order for output
    version_assign: dict[int, int] = {}
    items = []
    for row in rows:
        version_assign[row.candidate_id] = version_assign.get(row.candidate_id, 0) + 1
        ver = version_map.get(row.candidate_id, 1) - version_assign[row.candidate_id] + 1

        initials = ((row.first_name[:1] or "?") + (row.last_name[:1] or "?")).upper()
        color = avatar_colors[row.candidate_id % len(avatar_colors)]
        author = _author_name_from_email(row.author_email)
        fname = row.original_name or f"perfil_{row.application_id}.docx"

        items.append(VacancyDocumentItem(
            id=row.id,
            application_id=row.application_id,
            candidate_id=row.candidate_id,
            candidate_name=f"{row.first_name} {row.last_name}",
            candidate_initials=initials,
            candidate_avatar_color=color,
            stage_name_at_generation=row.stage_name,
            file_name=fname,
            file_id=row.file_id,
            stored_key=row.stored_key,
            version=ver,
            generated_by=author,
            generated_at=row.created_at,
        ))

    return items


@router.get(
    "/{vacancy_id}/pipeline",
    response_model=PipelineSchema,
    dependencies=[Depends(require_permission("recruitment.vacancies.read"))],
)
async def get_vacancy_pipeline(
    vacancy_id: int, session: SessionDep, current_user: CurrentUserDep
) -> PipelineSchema:
    forbid_candidate_portal(current_user)
    data = await PipelineRepository(session).get_pipeline(vacancy_id)

    avatar_colors = [
        "bg-primary-600", "bg-accent-500", "bg-primary-400",
        "bg-primary-700", "bg-accent-600", "bg-primary-300",
        "bg-accent-400", "bg-primary-500",
    ]

    # Expose a sequential 1..N display position instead of the stored sort key:
    # the final stage is seeded at a reserved high order (9999) to always sort
    # last, and that internal value must never reach the Kanban badge.
    stages = [
        PipelineStageSchema(
            id=str(s.id),
            vacancyId=str(vacancy_id),
            name=s.name,
            order=position,
            type="final" if s.is_final_positive else "normal",
        )
        for position, s in enumerate(data.stages, start=1)
    ]

    # Virtual "Rechazado" stage — always appended last so the board always shows it.
    # BUG-22: Note that stageId="rejected" is a sentinel string recognized by the
    # frontend to render the rejected candidates column.
    stages.append(PipelineStageSchema(
        id="rejected",
        vacancyId=str(vacancy_id),
        name="Rechazados",
        order=len(stages) + 1,
        type="rejected",
    ))

    cards = [
        PipelineCardSchema(
            id=str(c.id),
            candidateId=str(c.candidate_id),
            vacancyId=str(c.vacancy_id),
            stageId=str(c.current_stage_id) if c.current_stage_id else "rejected",
            candidateName=f"{c.first_name} {c.last_name}",
            initials=((c.first_name[:1] or "?") + (c.last_name[:1] or "?")).upper(),
            avatarColor=avatar_colors[c.candidate_id % len(avatar_colors)],
            avatarFileId=c.avatar_file_id,
            matchPercent=float(c.match_score) if c.match_score else None,
            matchStatus="done" if c.match_score else "analyzing",
            stageStatus="pending_review",
            salaryExpectation=int(c.salary_expectation) if c.salary_expectation else 0,
            updatedAt=(c.updated_at.isoformat() if c.updated_at else c.created_at.isoformat()),
        )
        for c in data.cards
    ]

    return PipelineSchema(
        stages=stages,
        cards=cards,
        rejectionSummary={"total": data.rejected_count, "reasons": []},
        hiredCount=data.hired_count,
        openings=data.openings,
    )
