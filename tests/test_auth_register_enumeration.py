"""Registration must not leak whether an email is already registered.

A new email and an already-registered email must produce an identical response
(status + body). The real owner of an existing account is notified by email
instead — that side effect is stubbed here so the test stays hermetic.
"""

import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.main import app

_PASSWORD = "StrongPass123!"


@pytest.fixture
async def client(session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def _override() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


class _NoOpTaskQueue:
    """Records enqueued task names and swallows them (no real SMTP here)."""

    def __init__(self) -> None:
        self.enqueued: list[str] = []

    async def enqueue(self, task_name: str, *args: object) -> None:
        self.enqueued.append(task_name)


@pytest.fixture(autouse=True)
def task_queue() -> _NoOpTaskQueue:
    # Overrides conftest's awaiting queue: these tests care about the response
    # parity and about WHICH email is sent, never about real delivery.
    queue = _NoOpTaskQueue()
    app.state.task_queue = queue
    return queue


async def test_new_and_existing_email_are_indistinguishable(
    client: AsyncClient, session: AsyncSession
) -> None:
    email = f"enum-{uuid.uuid4().hex[:12]}@test.example.com"
    body = {"email": email, "password": _PASSWORD, "accepts_terms": True}

    first = await client.post("/api/v1/auth/register", json=body)
    second = await client.post("/api/v1/auth/register", json=body)

    # New account and re-registration of the same email look identical.
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json() == second.json()
    # And the message never confirms the email exists.
    assert "ya está registrado" not in second.text
    assert "registrad" in second.json()["message"].lower()


async def test_reregister_inactive_email_is_indistinguishable(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Re-registering an INACTIVE account's email is indistinguishable from a new one.

    register_candidate looks up the email including inactive rows and treats a
    deactivated candidate as a reactivation (reusing the same row), not a new
    insert. Either way the response stays generic — it never leaks that the
    account exists — and no second row is created.
    """
    from sqlalchemy import func, select

    from app.core.security import hash_password
    from app.modules.auth.infrastructure.models import User
    from app.modules.org.infrastructure.parameters_repository import ParameterRepository

    portal = await ParameterRepository(session).get_by_type_and_code(
        "user_portal", "candidate"
    )
    assert portal is not None
    email = f"inactive-{uuid.uuid4().hex[:12]}@test.example.com"
    session.add(
        User(
            email=email,
            password_hash=hash_password(_PASSWORD),
            portal_id=portal.id,
            email_verified=True,
            is_active=False,
        )
    )
    await session.flush()

    response = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": _PASSWORD, "accepts_terms": True},
    )

    # Generic success response, and NO second user row for this email.
    assert response.status_code == 201
    assert "ya está registrado" not in response.text
    count = (
        await session.execute(
            select(func.count()).select_from(User).where(func.lower(User.email) == email.lower())
        )
    ).scalar_one()
    assert count == 1


async def test_reregister_unverified_email_sends_verification_not_account_exists(
    client: AsyncClient, session: AsyncSession, task_queue: _NoOpTaskQueue
) -> None:
    """An unverified account re-registering must get a FRESH verification link.

    Before this branch existed the route sent `send_account_exists_email`, whose only
    call to action is "log in" — which is exactly what an unverified account cannot do.
    The response stays byte-identical to every other branch (anti-enumeration); only
    the email that reaches the real inbox changes.
    """
    from app.core.security import hash_password
    from app.modules.auth.infrastructure.models import User as UserModel
    from app.modules.org.infrastructure.parameters_repository import ParameterRepository

    portal = await ParameterRepository(session).get_by_type_and_code(
        "user_portal", "candidate"
    )
    assert portal is not None
    email = f"unverified-{uuid.uuid4().hex[:12]}@test.example.com"
    session.add(
        UserModel(
            email=email,
            password_hash=hash_password(_PASSWORD),
            portal_id=portal.id,
            email_verified=False,
            is_active=True,
        )
    )
    await session.flush()

    response = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": _PASSWORD, "accepts_terms": True},
    )

    assert response.status_code == 201
    assert "ya está registrado" not in response.text
    assert task_queue.enqueued == ["send_verification_email"]
