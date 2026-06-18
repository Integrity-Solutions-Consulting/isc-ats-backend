from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File as FastAPIFile,
    HTTPException,
    Query,
    UploadFile,
    status,
)

from app.core.dependencies import CurrentUserDep, SessionDep
from app.modules.auth.api.authorization import require_permission
from app.modules.auth.infrastructure.models import User
from app.modules.org.infrastructure.models import Parameter
from app.modules.recruitment.api.candidates_schemas import (
    CandidateCreate,
    CandidateExpandedRead,
    CandidateRead,
    CandidateUpdate,
    CvPrefillResponse,
    RegistrationCatalogResponse,
)
from app.modules.recruitment.application.candidates_service import (
    CandidateNotFoundError,
    CandidateReferenceError,
    CandidateService,
    DuplicateCandidateError,
)
from app.modules.recruitment.infrastructure.candidates_repository import (
    CandidateRepository,
)
from app.modules.recruitment.infrastructure.candidates_expanded import (
    CandidatesExpandedRepository,
)
from app.modules.storage.infrastructure.models import File
from app.modules.ai.application.cv_parse_service import parse_candidate_cv
from app.shared.ownership import (
    forbid_candidate_portal,
    is_candidate_portal,
    require_owner,
)
from app.shared.pagination import Page, PageParams
from app.shared.repository import BaseRepository

router = APIRouter(prefix="/candidates", tags=["recruitment · candidates"])


def get_service(session: SessionDep) -> CandidateService:
    return CandidateService(
        CandidateRepository(session),
        BaseRepository(session, User),
        BaseRepository(session, Parameter),
        BaseRepository(session, File),
        CandidatesExpandedRepository(session),
    )


ServiceDep = Annotated[CandidateService, Depends(get_service)]


@router.get(
    "",
    response_model=Page[CandidateRead],
    dependencies=[Depends(require_permission("recruitment.candidates.read"))],
)
async def list_candidates(
    service: ServiceDep,
    current_user: CurrentUserDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[CandidateRead]:
    forbid_candidate_portal(current_user)
    params = PageParams(page=page, size=size)
    items, total = await service.list(params)
    return Page.create([CandidateRead.model_validate(i) for i in items], total, params)


# Static routes must be registered BEFORE /{candidate_id} so Starlette doesn't
# match "expanded" as a candidate_id path parameter.
@router.get(
    "/expanded",
    response_model=Page[CandidateExpandedRead],
    dependencies=[Depends(require_permission("recruitment.candidates.read"))],
)
async def list_candidates_expanded(
    service: ServiceDep,
    current_user: CurrentUserDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 50,
    user_id: Annotated[int | None, Query()] = None,
) -> Page[CandidateExpandedRead]:
    # Candidates only ever see their own row, whatever user_id they ask for.
    if is_candidate_portal(current_user):
        user_id = current_user.user_id
    params = PageParams(page=page, size=size)
    items, total = await service.list_expanded(params, user_id=user_id)
    return Page.create(
        [CandidateExpandedRead(**vars(item)) for item in items], total, params
    )


@router.get("/registration-catalog", response_model=RegistrationCatalogResponse)
async def registration_catalog(
    session: SessionDep,
    current_user: CurrentUserDep,
) -> RegistrationCatalogResponse:
    """Return all reference catalogs needed by the registration form.

    Accessible to any authenticated user (no org.parameters.read required).
    """
    from sqlalchemy import select

    TYPES = ("city", "education_level", "career", "title", "university")
    results = await session.execute(
        select(Parameter)
        .where(Parameter.type.in_(TYPES))
        .where(Parameter.is_active.is_(True))
        .order_by(Parameter.type, Parameter.name)
    )
    params = results.scalars().all()

    def to_options(type_: str) -> list[dict]:
        return [
            {"id": p.id, "code": p.code, "name": p.name}
            for p in params if p.type == type_
        ]

    return RegistrationCatalogResponse(
        cities=to_options("city"),
        educationLevels=to_options("education_level"),
        careers=to_options("career"),
        titles=to_options("title"),
        universities=to_options("university"),
    )


@router.post("/cv/prefill", response_model=CvPrefillResponse)
async def cv_prefill(
    session: SessionDep,
    current_user: CurrentUserDep,
    file: Annotated[UploadFile, FastAPIFile(...)],
) -> CvPrefillResponse:
    """Extract personal+education data from an uploaded CV and match catalog IDs.

    The PDF is processed transiently in memory and NEVER persisted — storage
    only happens later, when the candidate finishes registration. This keeps
    pre-fill compliant with data-minimisation (no CV stored without consent).
    """
    from app.modules.ai.application.cv_prefill_service import prefill_from_bytes
    pdf_bytes = await file.read()
    result = await prefill_from_bytes(pdf_bytes, session)
    return CvPrefillResponse(**result)


@router.get(
    "/{candidate_id}",
    response_model=CandidateRead,
    dependencies=[Depends(require_permission("recruitment.candidates.read"))],
)
async def get_candidate(
    candidate_id: int, service: ServiceDep, current_user: CurrentUserDep
) -> CandidateRead:
    try:
        candidate = await service.get(candidate_id)
    except CandidateNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    require_owner(current_user, candidate.user_id)
    return CandidateRead.model_validate(candidate)


@router.post(
    "",
    response_model=CandidateRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("recruitment.candidates.create"))],
)
async def create_candidate(
    data: CandidateCreate,
    service: ServiceDep,
    current_user: CurrentUserDep,
    background_tasks: BackgroundTasks,
) -> CandidateRead:
    # A candidate may only create their own profile (user_id == token subject).
    require_owner(current_user, data.user_id)
    try:
        created = await service.create(data, current_user)
    except CandidateReferenceError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except DuplicateCandidateError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if created.cv_file_id:
        background_tasks.add_task(parse_candidate_cv, created.id)
    return CandidateRead.model_validate(created)


@router.patch(
    "/{candidate_id}",
    response_model=CandidateRead,
    dependencies=[Depends(require_permission("recruitment.candidates.update"))],
)
async def update_candidate(
    candidate_id: int,
    data: CandidateUpdate,
    service: ServiceDep,
    current_user: CurrentUserDep,
    background_tasks: BackgroundTasks,
) -> CandidateRead:
    try:
        existing = await service.get(candidate_id)
    except CandidateNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    require_owner(current_user, existing.user_id)
    try:
        updated = await service.update(candidate_id, data, current_user)
    except CandidateNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except CandidateReferenceError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except DuplicateCandidateError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if data.cv_file_id is not None:
        background_tasks.add_task(parse_candidate_cv, candidate_id)
    return CandidateRead.model_validate(updated)


@router.delete(
    "/{candidate_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("recruitment.candidates.delete"))],
)
async def delete_candidate(candidate_id: int, service: ServiceDep) -> None:
    try:
        await service.delete(candidate_id)
    except CandidateNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.get(
    "/{candidate_id}/expanded",
    response_model=CandidateExpandedRead,
    dependencies=[Depends(require_permission("recruitment.candidates.read"))],
)
async def get_candidate_expanded(
    candidate_id: int, session: SessionDep, current_user: CurrentUserDep
) -> CandidateExpandedRead:
    item = await CandidatesExpandedRepository(session).get_expanded(candidate_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Candidate {candidate_id} not found")
    require_owner(current_user, item.user_id)
    return CandidateExpandedRead(**vars(item))
