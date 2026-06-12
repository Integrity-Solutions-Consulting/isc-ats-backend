from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.dependencies import CurrentUserDep, SessionDep
from app.modules.ai.api.ai_usage_logs_schemas import AiUsageLogCreate, AiUsageLogRead
from app.modules.ai.application.ai_usage_logs_service import (
    AiUsageLogNotFoundError,
    AiUsageLogService,
)
from app.modules.ai.infrastructure.models import AiUsageLog
from app.modules.auth.api.authorization import require_permission
from app.shared.pagination import Page, PageParams
from app.shared.repository import BaseRepository

router = APIRouter(prefix="/ai-usage-logs", tags=["ai · usage logs"])


def get_service(session: SessionDep) -> AiUsageLogService:
    return AiUsageLogService(BaseRepository(session, AiUsageLog))


ServiceDep = Annotated[AiUsageLogService, Depends(get_service)]


@router.get(
    "",
    response_model=Page[AiUsageLogRead],
    dependencies=[Depends(require_permission("ai.ai_usage_logs.read"))],
)
async def list_ai_usage_logs(
    service: ServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    action: Annotated[str | None, Query()] = None,
) -> Page[AiUsageLogRead]:
    params = PageParams(page=page, size=size)
    items, total = await service.list(params, action=action)
    return Page.create([AiUsageLogRead.model_validate(i) for i in items], total, params)


@router.get(
    "/{log_id}",
    response_model=AiUsageLogRead,
    dependencies=[Depends(require_permission("ai.ai_usage_logs.read"))],
)
async def get_ai_usage_log(log_id: int, service: ServiceDep) -> AiUsageLogRead:
    try:
        return AiUsageLogRead.model_validate(await service.get(log_id))
    except AiUsageLogNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post(
    "",
    response_model=AiUsageLogRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("ai.ai_usage_logs.create"))],
)
async def create_ai_usage_log(
    data: AiUsageLogCreate, service: ServiceDep, current_user: CurrentUserDep
) -> AiUsageLogRead:
    return AiUsageLogRead.model_validate(await service.create(data, current_user))
