import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.security import create_verification_token
from app.main import app
from app.modules.auth.infrastructure.models import User
from app.modules.auth.infrastructure.repository import UserRepository
from app.modules.org.infrastructure.parameters_repository import ParameterRepository

VERIFY_URL = "/api/v1/auth/verify"


@pytest.fixture
async def client(session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def _override() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def _candidate_portal_id(session: AsyncSession) -> int:
    portal = await ParameterRepository(session).get_by_type_and_code("user_portal", "candidate")
    assert portal is not None
    return portal.id


async def _make_unverified_user(session: AsyncSession) -> User:
    portal_id = await _candidate_portal_id(session)
    return await UserRepository(session).add(
        User(
            email=f"{uuid.uuid4().hex[:12]}@test.local",
            portal_id=portal_id,
            email_verified=False,
        )
    )


async def test_verify_email_success(client: AsyncClient, session: AsyncSession) -> None:
    user = await _make_unverified_user(session)
    token = create_verification_token(user.id)

    response = await client.post(VERIFY_URL, json={"token": token})
    assert response.status_code == 200
    assert "verificado" in response.json()["message"].lower()

    # Verify database state
    refreshed = await UserRepository(session).get(user.id)
    assert refreshed is not None
    assert refreshed.email_verified is True


async def test_verify_email_rejects_replay(client: AsyncClient, session: AsyncSession) -> None:
    user = await _make_unverified_user(session)
    token = create_verification_token(user.id)

    # First verification should succeed
    r1 = await client.post(VERIFY_URL, json={"token": token})
    assert r1.status_code == 200

    # Second verification with same token (replay) should fail
    r2 = await client.post(VERIFY_URL, json={"token": token})
    assert r2.status_code == 400
    assert "ya fue utilizado" in r2.json()["detail"].lower() or "expirado" in r2.json()["detail"].lower()


async def test_verify_email_rejects_invalid_token(client: AsyncClient) -> None:
    response = await client.post(VERIFY_URL, json={"token": "invalid-token-here"})
    assert response.status_code == 400
    assert "inválido" in response.json()["detail"].lower() or "expirado" in response.json()["detail"].lower()
