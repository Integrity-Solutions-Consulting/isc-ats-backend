import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import CurrentUser
from app.modules.auth.infrastructure.models import User
from app.modules.comms.api.email_logs_schemas import EmailLogCreate
from app.modules.comms.api.notifications_schemas import NotificationCreate
from app.modules.comms.application.email_logs_service import (
    EmailLogNotFoundError,
    EmailLogReferenceError,
    EmailLogService,
)
from app.modules.comms.application.notifications_service import (
    NotificationNotFoundError,
    NotificationReferenceError,
    NotificationService,
)
from app.modules.comms.infrastructure.models import EmailLog, Notification
from app.modules.org.infrastructure.models import Parameter
from app.modules.org.infrastructure.parameters_repository import ParameterRepository
from app.shared.pagination import PageParams
from app.shared.repository import BaseRepository

ACTOR = CurrentUser(user_id=1, ip="127.0.0.1")


def _notif_service(session: AsyncSession) -> NotificationService:
    return NotificationService(
        BaseRepository(session, Notification),
        BaseRepository(session, User),
        BaseRepository(session, Parameter),
    )


def _email_service(session: AsyncSession) -> EmailLogService:
    return EmailLogService(
        BaseRepository(session, EmailLog),
        BaseRepository(session, Parameter),
    )


async def _make_user(session: AsyncSession) -> User:
    portal = await ParameterRepository(session).get_by_type_and_code("user_portal", "staff")
    assert portal is not None
    return await BaseRepository(session, User).add(
        User(email=f"{uuid.uuid4().hex[:12]}@test.local", portal_id=portal.id)
    )


async def _make_param(session: AsyncSession) -> Parameter:
    return await BaseRepository(session, Parameter).add(
        Parameter(type="x", code=uuid.uuid4().hex[:8], name="P")
    )


# ── Notifications ──────────────────────────────────────────────────────────────


async def test_create_notification(session: AsyncSession) -> None:
    user = await _make_user(session)
    n = await _notif_service(session).create(
        NotificationCreate(recipient_id=user.id, title="Hello"), ACTOR
    )

    assert n.id is not None
    assert n.recipient_id == user.id
    assert n.read_at is None
    assert n.is_active is True


async def test_create_notification_rejects_unknown_recipient(session: AsyncSession) -> None:
    with pytest.raises(NotificationReferenceError):
        await _notif_service(session).create(
            NotificationCreate(recipient_id=999999, title="X"), ACTOR
        )


async def test_create_notification_rejects_unknown_channel(session: AsyncSession) -> None:
    user = await _make_user(session)
    with pytest.raises(NotificationReferenceError):
        await _notif_service(session).create(
            NotificationCreate(recipient_id=user.id, title="X", channel_id=999999), ACTOR
        )


async def test_mark_notification_read(session: AsyncSession) -> None:
    user = await _make_user(session)
    svc = _notif_service(session)
    n = await svc.create(NotificationCreate(recipient_id=user.id, title="Hi"), ACTOR)

    assert n.read_at is None
    n2 = await svc.mark_read(n.id)
    assert n2.read_at is not None


async def test_mark_read_idempotent(session: AsyncSession) -> None:
    user = await _make_user(session)
    svc = _notif_service(session)
    n = await svc.create(NotificationCreate(recipient_id=user.id, title="Hi"), ACTOR)
    first = await svc.mark_read(n.id)
    second = await svc.mark_read(n.id)
    assert first.read_at == second.read_at


async def test_delete_notification(session: AsyncSession) -> None:
    user = await _make_user(session)
    svc = _notif_service(session)
    n = await svc.create(NotificationCreate(recipient_id=user.id, title="Bye"), ACTOR)
    await svc.delete(n.id)

    with pytest.raises(NotificationNotFoundError):
        await svc.get(n.id)


async def test_list_notifications_unread_only(session: AsyncSession) -> None:
    user = await _make_user(session)
    svc = _notif_service(session)
    n1 = await svc.create(NotificationCreate(recipient_id=user.id, title="A"), ACTOR)
    n2 = await svc.create(NotificationCreate(recipient_id=user.id, title="B"), ACTOR)
    await svc.mark_read(n1.id)

    items, total = await svc.list(
        PageParams(page=1, size=20),
        recipient_id=user.id,
        unread_only=True,
    )
    assert total == 1
    assert items[0].id == n2.id


# ── Email logs ─────────────────────────────────────────────────────────────────


async def test_create_email_log(session: AsyncSession) -> None:
    param = await _make_param(session)
    log = await _email_service(session).create(
        EmailLogCreate(to_email="x@test.com", status_id=param.id), ACTOR
    )

    assert log.id is not None
    assert log.to_email == "x@test.com"
    assert log.is_active is True


async def test_create_email_log_rejects_unknown_status(session: AsyncSession) -> None:
    with pytest.raises(EmailLogReferenceError):
        await _email_service(session).create(
            EmailLogCreate(to_email="x@test.com", status_id=999999), ACTOR
        )


async def test_get_email_log_not_found(session: AsyncSession) -> None:
    with pytest.raises(EmailLogNotFoundError):
        await _email_service(session).get(999999)


async def test_list_email_logs_filtered_by_status(session: AsyncSession) -> None:
    p1 = await _make_param(session)
    p2 = await _make_param(session)
    svc = _email_service(session)
    await svc.create(EmailLogCreate(to_email="a@x.com", status_id=p1.id), ACTOR)
    await svc.create(EmailLogCreate(to_email="b@x.com", status_id=p2.id), ACTOR)

    items, total = await svc.list(PageParams(page=1, size=20), status_id=p1.id)
    assert total == 1
    assert items[0].status_id == p1.id
