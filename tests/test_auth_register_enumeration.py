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
    """Swallows enqueued tasks so registration never triggers real SMTP here."""

    async def enqueue(self, task_name: str, *args: object) -> None:
        return None


@pytest.fixture(autouse=True)
def _stub_task_queue() -> None:
    # Overrides conftest's awaiting queue: this test only cares about the response
    # parity, not the email side effect.
    app.state.task_queue = _NoOpTaskQueue()


async def test_new_and_existing_email_are_indistinguishable(
    client: AsyncClient, session: AsyncSession
) -> None:
    email = f"enum-{uuid.uuid4().hex[:12]}@test.example.com"
    body = {"email": email, "password": _PASSWORD}

    first = await client.post("/api/v1/auth/register", json=body)
    second = await client.post("/api/v1/auth/register", json=body)

    # New account and re-registration of the same email look identical.
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json() == second.json()
    # And the message never confirms the email exists.
    assert "ya está registrado" not in second.text
    assert "registrad" in second.json()["message"].lower()
