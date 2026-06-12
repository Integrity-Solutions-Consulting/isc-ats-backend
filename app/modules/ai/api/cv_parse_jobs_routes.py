from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.dependencies import CurrentUserDep, SessionDep
from app.modules.ai.api.cv_parse_jobs_schemas import (
    CvParseJobCreate,
    CvParseJobRead,
    CvParseJobUpdate,
)
from app.modules.ai.application.cv_parse_jobs_service import (
    CvParseJobNotFoundError,
    CvParseJobReferenceError,
    CvParseJobService,
)
from app.modules.ai.infrastructure.models import CvParseJob
from app.modules.auth.api.authorization import require_permission
from app.modules.org.infrastructure.models import Parameter
from app.modules.recruitment.infrastructure.candidate_models import Candidate
from app.modules.storage.infrastructure.models import File
from app.shared.pagination import Page, PageParams
from app.shared.repository import BaseRepository

router = APIRouter(prefix="/cv-parse-jobs", tags=["ai · cv parse jobs"])


def get_service(session: SessionDep) -> CvParseJobService:
    return CvParseJobService(
        BaseRepository(session, CvParseJob),
        BaseRepository(session, File),
        BaseRepository(session, Candidate),
        BaseRepository(session, Parameter),
    )


ServiceDep = Annotated[CvParseJobService, Depends(get_service)]


@router.get(
    "",
    response_model=Page[CvParseJobRead],
    dependencies=[Depends(require_permission("ai.cv_parse_jobs.read"))],
)
async def list_cv_parse_jobs(
    service: ServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    candidate_id: Annotated[int | None, Query()] = None,
    status_id: Annotated[int | None, Query()] = None,
) -> Page[CvParseJobRead]:
    params = PageParams(page=page, size=size)
    items, total = await service.list(params, candidate_id=candidate_id, status_id=status_id)
    return Page.create([CvParseJobRead.model_validate(i) for i in items], total, params)


@router.get(
    "/{job_id}",
    response_model=CvParseJobRead,
    dependencies=[Depends(require_permission("ai.cv_parse_jobs.read"))],
)
async def get_cv_parse_job(job_id: int, service: ServiceDep) -> CvParseJobRead:
    try:
        return CvParseJobRead.model_validate(await service.get(job_id))
    except CvParseJobNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post(
    "",
    response_model=CvParseJobRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("ai.cv_parse_jobs.create"))],
)
async def create_cv_parse_job(
    data: CvParseJobCreate, service: ServiceDep, current_user: CurrentUserDep
) -> CvParseJobRead:
    try:
        created = await service.create(data, current_user)
    except CvParseJobReferenceError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return CvParseJobRead.model_validate(created)


@router.patch(
    "/{job_id}",
    response_model=CvParseJobRead,
    dependencies=[Depends(require_permission("ai.cv_parse_jobs.update"))],
)
async def update_cv_parse_job(
    job_id: int,
    data: CvParseJobUpdate,
    service: ServiceDep,
    current_user: CurrentUserDep,
) -> CvParseJobRead:
    try:
        updated = await service.update(job_id, data, current_user)
    except CvParseJobNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except CvParseJobReferenceError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return CvParseJobRead.model_validate(updated)


@router.delete(
    "/{job_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("ai.cv_parse_jobs.delete"))],
)
async def delete_cv_parse_job(job_id: int, service: ServiceDep) -> None:
    try:
        await service.delete(job_id)
    except CvParseJobNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
