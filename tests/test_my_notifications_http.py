"""HTTP tests for candidate self-service notifications (/comms/notifications/me).

Any authenticated user (candidate or staff) can list and mark-read ONLY their
own notifications, WITHOUT holding the staff comms.notifications.* permissions.
Ownership is by recipient_id == current_user; another user's notification is
invisible (404 on mark-read, excluded from listings).
"""

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.security import create_access_token
from app.main import app
from app.modules.auth.infrastructure.models import User
from app.modules.comms.infrastructure.models import Notification
from app.modules.org.infrastructure.parameters_repository import ParameterRepository
from app.shared.repository import BaseRepository

ME_URL = "/api/v1/comms/notifications/me"


@pytest.fixture
async def client(session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def _use_test_session() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_session] = _use_test_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


def _bearer(user_id: int, portal: str = "candidate") -> dict[str, str]:
    token = create_access_token(user_id, extra_claims={"portal": portal})
    return {"Authorization": f"Bearer {token}"}


async def _make_user(session: AsyncSession) -> User:
    portal = await ParameterRepository(session).get_by_type_and_code(
        "user_portal", "candidate"
    )
    assert portal is not None, "user_portal:candidate must be seeded"
    return await BaseRepository(session, User).add(
        User(email=f"{uuid.uuid4().hex[:12]}@notif.local", portal_id=portal.id)
    )


async def _make_notification(
    session: AsyncSession, recipient_id: int, *, read: bool = False, title: str = "N"
) -> Notification:
    n = Notification(recipient_id=recipient_id, title=title, created_by=recipient_id)
    if read:
        n.read_at = datetime.now(UTC)
    return await BaseRepository(session, Notification).add(n)


async def test_me_requires_auth(client: AsyncClient) -> None:
    response = await client.get(ME_URL)
    assert response.status_code in (401, 403)


async def test_me_lists_only_own_notifications(
    client: AsyncClient, session: AsyncSession
) -> None:
    user_a = await _make_user(session)
    user_b = await _make_user(session)
    await _make_notification(session, user_a.id, title="ForA")
    await _make_notification(session, user_b.id, title="ForB")

    response = await client.get(ME_URL, headers=_bearer(user_a.id))

    assert response.status_code == 200
    items = response.json()["items"]
    assert all(item["recipient_id"] == user_a.id for item in items)
    assert any(item["title"] == "ForA" for item in items)
    assert all(item["title"] != "ForB" for item in items)


async def test_me_unread_only_filter(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await _make_user(session)
    await _make_notification(session, user.id, read=False, title="Unread")
    await _make_notification(session, user.id, read=True, title="Read")

    response = await client.get(
        f"{ME_URL}?unread_only=true", headers=_bearer(user.id)
    )

    assert response.status_code == 200
    items = response.json()["items"]
    assert all(item["read_at"] is None for item in items)
    assert any(item["title"] == "Unread" for item in items)


async def test_me_unread_count(client: AsyncClient, session: AsyncSession) -> None:
    user = await _make_user(session)
    await _make_notification(session, user.id, read=False)
    await _make_notification(session, user.id, read=False)
    await _make_notification(session, user.id, read=True)

    response = await client.get(f"{ME_URL}/unread-count", headers=_bearer(user.id))

    assert response.status_code == 200
    assert response.json()["count"] == 2


async def test_me_unread_count_excludes_other_users(
    client: AsyncClient, session: AsyncSession
) -> None:
    user_a = await _make_user(session)
    user_b = await _make_user(session)
    await _make_notification(session, user_a.id, read=False)
    await _make_notification(session, user_b.id, read=False)

    response = await client.get(f"{ME_URL}/unread-count", headers=_bearer(user_a.id))

    assert response.status_code == 200
    assert response.json()["count"] == 1


async def test_me_mark_own_read(client: AsyncClient, session: AsyncSession) -> None:
    user = await _make_user(session)
    n = await _make_notification(session, user.id, read=False)

    response = await client.patch(
        f"{ME_URL}/{n.id}/read", headers=_bearer(user.id)
    )

    assert response.status_code == 200
    assert response.json()["read_at"] is not None


async def test_me_cannot_mark_anothers_read(
    client: AsyncClient, session: AsyncSession
) -> None:
    user_a = await _make_user(session)
    user_b = await _make_user(session)
    n = await _make_notification(session, user_a.id, read=False)

    response = await client.patch(
        f"{ME_URL}/{n.id}/read", headers=_bearer(user_b.id)
    )

    assert response.status_code == 404
