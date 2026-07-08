"""Registration refuses disposable / throwaway email domains.

Registration is the abuse funnel — per-IP rate limits are defeated by IP rotation
and the attacker used yopmail — so throwaway domains are blocked. Domain-level
rejection reveals nothing about whether an email is already registered.
"""

import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.main import app
from app.modules.auth.application.disposable_email import is_disposable_email

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
    """Swallow enqueued tasks so registration never triggers real SMTP here."""

    async def enqueue(self, task_name: str, *args: object) -> None:
        return None


@pytest.fixture(autouse=True)
def _stub_task_queue() -> None:
    app.state.task_queue = _NoOpTaskQueue()


@pytest.mark.parametrize(
    "email",
    [
        "attacker@yopmail.com",
        "x@yopmail.net",
        "y@mailinator.com",
        "z@guerrillamail.com",
        "sub@foo.yopmail.com",  # subdomain of a blocked domain
        "MixedCase@YOPMAIL.com",  # case-insensitive
    ],
)
def test_is_disposable_email_true(email: str) -> None:
    assert is_disposable_email(email) is True


@pytest.mark.parametrize(
    "email",
    [
        "real.person@gmail.com",
        "empleado@integritysolutions.com.ec",
        "someone@outlook.com",
        "candidate@test.example.com",
    ],
)
def test_is_disposable_email_false(email: str) -> None:
    assert is_disposable_email(email) is False


async def test_register_rejects_disposable_email(
    client: AsyncClient, session: AsyncSession
) -> None:
    res = await client.post(
        "/api/v1/auth/register",
        json={"email": "spam@yopmail.com", "password": _PASSWORD},
    )
    assert res.status_code == 422


async def test_register_accepts_real_email(
    client: AsyncClient, session: AsyncSession
) -> None:
    email = f"real-{uuid.uuid4().hex[:12]}@test.example.com"
    res = await client.post(
        "/api/v1/auth/register", json={"email": email, "password": _PASSWORD}
    )
    assert res.status_code == 201
