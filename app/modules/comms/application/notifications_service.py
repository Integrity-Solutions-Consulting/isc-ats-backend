import logging
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import CurrentUser
from app.modules.auth.infrastructure.models import Role, User, UserRole
from app.modules.comms.api.notifications_schemas import NotificationCreate
from app.modules.comms.application.email_dispatch_service import EmailDispatchService
from app.modules.comms.application.email_sender import EmailMessage
from app.modules.comms.application.email_templates import RenderedEmail
from app.modules.comms.infrastructure.email_sender_factory import build_email_sender
from app.modules.comms.infrastructure.models import Notification
from app.modules.org.infrastructure.models import Parameter
from app.shared.pagination import PageParams
from app.shared.repository import BaseRepository

logger = logging.getLogger(__name__)


async def notify_role(
    session: AsyncSession,
    *,
    role_name: str,
    title: str,
    body: str,
    related_entity_type: str,
    related_entity_id: int,
    email_render: Callable[[str, str], RenderedEmail] | None = None,
) -> int:
    """Fan-out: insert one in-app Notification per active user of the named role.

    Resolves the role by NAME (never by id — ids differ per environment), selects
    only users where UserRole.is_active AND User.is_active are both True, and
    inserts exactly one Notification per matching user.

    If ``email_render`` is provided, dispatches one email per notified user via
    ``EmailDispatchService``.  Each email dispatch is wrapped in its own try/except
    so a single SMTP failure is swallowed — it MUST NOT roll back the in-app
    notifications and MUST NOT propagate to the caller.

    Returns the count of users notified (in-app Notification rows inserted).
    """
    # Resolve role by name — never hardcode an id.
    role_row = (
        await session.execute(
            select(Role).where(Role.name == role_name).where(Role.is_active.is_(True))
        )
    ).scalar_one_or_none()

    if role_row is None:
        logger.warning("notify_role: role '%s' not found — no notifications sent", role_name)
        return 0

    # Fetch all active users that hold an active assignment to this role.
    stmt = (
        select(User)
        .join(UserRole, UserRole.user_id == User.id)
        .where(UserRole.role_id == role_row.id)
        .where(UserRole.is_active.is_(True))
        .where(User.is_active.is_(True))
    )
    users = list((await session.execute(stmt)).scalars().all())

    if not users:
        return 0

    # Insert in-app notifications for all matched users first (bulk, single flush).
    for user in users:
        session.add(
            Notification(
                recipient_id=user.id,
                title=title,
                body=body,
                related_entity_type=related_entity_type,
                related_entity_id=related_entity_id,
                created_by=None,
            )
        )
    await session.flush()

    # Dispatch emails individually — one failure must not affect others.
    if email_render is not None:
        dispatch = EmailDispatchService(session, build_email_sender())
        for user in users:
            try:
                rendered = email_render(user.email, title)
                await dispatch.send(
                    EmailMessage(
                        to_email=user.email,
                        subject=rendered.subject,
                        html_body=rendered.html_body,
                        text_body=rendered.text_body,
                    )
                )
            except Exception:
                logger.exception(
                    "notify_role: email dispatch failed for user %s — swallowed", user.id
                )

    return len(users)


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
            )
            .scalars()
            .all()
        )
        return items, total

    async def get(self, notification_id: int) -> Notification:
        n = await self.repository.get(notification_id)
        if n is None:
            raise NotificationNotFoundError(f"Notification {notification_id} not found")
        return n

    async def create(self, data: NotificationCreate, actor: CurrentUser) -> Notification:
        if await self.users.get(data.recipient_id) is None:
            raise NotificationReferenceError(f"recipient_id={data.recipient_id} not found")
        if data.channel_id is not None and await self.parameters.get(data.channel_id) is None:
            raise NotificationReferenceError(f"channel_id={data.channel_id} not found")
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

    async def count_unread(self, recipient_id: int) -> int:
        """Number of active, unread notifications addressed to a recipient."""
        stmt = (
            select(func.count())
            .select_from(Notification)
            .where(Notification.is_active.is_(True))
            .where(Notification.recipient_id == recipient_id)
            .where(Notification.read_at.is_(None))
        )
        return (await self.repository.session.execute(stmt)).scalar_one()

    async def mark_read_for_recipient(
        self, notification_id: int, recipient_id: int
    ) -> Notification:
        """Mark a notification read only if it belongs to `recipient_id`.

        Raises NotificationNotFoundError when the notification is missing OR
        owned by another user — existence is never leaked across recipients.
        """
        n = await self.get(notification_id)
        if n.recipient_id != recipient_id:
            raise NotificationNotFoundError(f"Notification {notification_id} not found")
        return await self.mark_read(notification_id)

    async def delete(self, notification_id: int) -> None:
        n = await self.get(notification_id)
        await self.repository.soft_delete(n)
