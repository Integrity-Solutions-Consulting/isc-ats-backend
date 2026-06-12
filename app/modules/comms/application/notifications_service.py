from datetime import UTC, datetime

from sqlalchemy import func, select

from app.core.dependencies import CurrentUser
from app.modules.auth.infrastructure.models import User
from app.modules.comms.api.notifications_schemas import NotificationCreate
from app.modules.comms.infrastructure.models import Notification
from app.modules.org.infrastructure.models import Parameter
from app.shared.pagination import PageParams
from app.shared.repository import BaseRepository


class NotificationNotFoundError(Exception):
    pass


class NotificationReferenceError(Exception):
    pass


class NotificationService:
    def __init__(
        self,
        repository: BaseRepository[Notification],
        users: BaseRepository[User],
        parameters: BaseRepository[Parameter],
    ) -> None:
        self.repository = repository
        self.users = users
        self.parameters = parameters

    async def list(
        self,
        params: PageParams,
        *,
        recipient_id: int | None = None,
        channel_id: int | None = None,
        unread_only: bool = False,
    ) -> tuple[list[Notification], int]:
        stmt = select(Notification).where(Notification.is_active.is_(True))
        if recipient_id is not None:
            stmt = stmt.where(Notification.recipient_id == recipient_id)
        if channel_id is not None:
            stmt = stmt.where(Notification.channel_id == channel_id)
        if unread_only:
            stmt = stmt.where(Notification.read_at.is_(None))

        session = self.repository.session
        total = (
            await session.execute(select(func.count()).select_from(stmt.subquery()))
        ).scalar_one()
        items = list(
            (
                await session.execute(
                    stmt.order_by(Notification.id).offset(params.offset).limit(params.limit)
                )
            ).scalars().all()
        )
        return items, total

    async def get(self, notification_id: int) -> Notification:
        n = await self.repository.get(notification_id)
        if n is None:
            raise NotificationNotFoundError(f"Notification {notification_id} not found")
        return n

    async def create(self, data: NotificationCreate, actor: CurrentUser) -> Notification:
        if await self.users.get(data.recipient_id) is None:
            raise NotificationReferenceError(
                f"recipient_id={data.recipient_id} not found"
            )
        if data.channel_id is not None and await self.parameters.get(data.channel_id) is None:
            raise NotificationReferenceError(
                f"channel_id={data.channel_id} not found"
            )
        n = Notification(
            **data.model_dump(),
            created_by=actor.user_id,
            ip_created=actor.ip,
        )
        return await self.repository.add(n)

    async def mark_read(self, notification_id: int) -> Notification:
        n = await self.get(notification_id)
        if n.read_at is None:
            n.read_at = datetime.now(UTC)
            await self.repository.session.flush()
            await self.repository.session.refresh(n)
        return n

    async def delete(self, notification_id: int) -> None:
        n = await self.get(notification_id)
        await self.repository.soft_delete(n)
