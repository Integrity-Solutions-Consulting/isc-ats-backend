from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.dependencies import CurrentUserDep, SessionDep
from app.modules.auth.api.authorization import require_permission
from app.modules.auth.infrastructure.models import User
from app.modules.comms.api.notifications_schemas import NotificationCreate, NotificationRead
from app.modules.comms.application.notifications_service import (
    NotificationNotFoundError,
    NotificationReferenceError,
    NotificationService,
)
from app.modules.comms.infrastructure.models import Notification
from app.modules.org.infrastructure.models import Parameter
from app.shared.pagination import Page, PageParams
from app.shared.repository import BaseRepository

router = APIRouter(prefix="/notifications", tags=["comms · notifications"])


def get_service(session: SessionDep) -> NotificationService:
    return NotificationService(
        BaseRepository(session, Notification),
        BaseRepository(session, User),
        BaseRepository(session, Parameter),
    )


ServiceDep = Annotated[NotificationService, Depends(get_service)]


@router.get(
    "",
    response_model=Page[NotificationRead],
    dependencies=[Depends(require_permission("comms.notifications.read"))],
)
async def list_notifications(
    service: ServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    recipient_id: Annotated[int | None, Query()] = None,
    channel_id: Annotated[int | None, Query()] = None,
    unread_only: Annotated[bool, Query()] = False,
) -> Page[NotificationRead]:
    params = PageParams(page=page, size=size)
    items, total = await service.list(
        params,
        recipient_id=recipient_id,
        channel_id=channel_id,
        unread_only=unread_only,
    )
    return Page.create([NotificationRead.model_validate(i) for i in items], total, params)


@router.get(
    "/{notification_id}",
    response_model=NotificationRead,
    dependencies=[Depends(require_permission("comms.notifications.read"))],
)
async def get_notification(
    notification_id: int, service: ServiceDep
) -> NotificationRead:
    try:
        return NotificationRead.model_validate(await service.get(notification_id))
    except NotificationNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post(
    "",
    response_model=NotificationRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("comms.notifications.create"))],
)
async def create_notification(
    data: NotificationCreate, service: ServiceDep, current_user: CurrentUserDep
) -> NotificationRead:
    try:
        created = await service.create(data, current_user)
    except NotificationReferenceError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return NotificationRead.model_validate(created)


@router.patch(
    "/{notification_id}/read",
    response_model=NotificationRead,
    dependencies=[Depends(require_permission("comms.notifications.update"))],
)
async def mark_notification_read(
    notification_id: int, service: ServiceDep
) -> NotificationRead:
    try:
        return NotificationRead.model_validate(await service.mark_read(notification_id))
    except NotificationNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.delete(
    "/{notification_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("comms.notifications.delete"))],
)
async def delete_notification(notification_id: int, service: ServiceDep) -> None:
    try:
        await service.delete(notification_id)
    except NotificationNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
