from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.dependencies import CurrentUserDep, SessionDep
from app.modules.auth.api.authorization import require_permission
from app.modules.comms.api.email_logs_schemas import EmailLogCreate, EmailLogRead
from app.modules.comms.application.email_logs_service import (
    EmailLogNotFoundError,
    EmailLogReferenceError,
    EmailLogService,
)
from app.modules.comms.infrastructure.models import EmailLog
from app.modules.org.infrastructure.models import Parameter
from app.shared.pagination import Page, PageParams
from app.shared.repository import BaseRepository

router = APIRouter(prefix="/email-logs", tags=["comms · email logs"])


def get_service(session: SessionDep) -> EmailLogService:
    return EmailLogService(
        BaseRepository(session, EmailLog),
        BaseRepository(session, Parameter),
    )


ServiceDep = Annotated[EmailLogService, Depends(get_service)]


@router.get(
    "",
    response_model=Page[EmailLogRead],
    dependencies=[Depends(require_permission("comms.email_logs.read"))],
)
async def list_email_logs(
    service: ServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    status_id: Annotated[int | None, Query()] = None,
) -> Page[EmailLogRead]:
    params = PageParams(page=page, size=size)
    items, total = await service.list(params, status_id=status_id)
    return Page.create([EmailLogRead.model_validate(i) for i in items], total, params)


@router.get(
    "/{log_id}",
    response_model=EmailLogRead,
    dependencies=[Depends(require_permission("comms.email_logs.read"))],
)
async def get_email_log(log_id: int, service: ServiceDep) -> EmailLogRead:
    try:
        return EmailLogRead.model_validate(await service.get(log_id))
    except EmailLogNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post(
    "",
    response_model=EmailLogRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("comms.email_logs.create"))],
)
async def create_email_log(
    data: EmailLogCreate, service: ServiceDep, current_user: CurrentUserDep
) -> EmailLogRead:
    try:
        created = await service.create(data, current_user)
    except EmailLogReferenceError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return EmailLogRead.model_validate(created)
