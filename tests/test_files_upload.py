"""Tests for POST /storage/files/upload — entity_type handling.

Covers:
- Default entity_type is "cv" (schema.sql vocabulary, no longer hardcoded).
- Explicit entity_type from the allowed vocabulary is persisted.
- Unknown entity_type → 422 before anything is uploaded to MinIO.
"""

import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.security import create_access_token, hash_password
from app.main import app
from app.modules.auth.infrastructure.models import User
from app.modules.auth.infrastructure.repository import UserRepository
from app.modules.org.infrastructure.parameters_repository import ParameterRepository


@pytest.fixture
async def client(session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def _override() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def _make_user(session: AsyncSession) -> User:
    portal = await ParameterRepository(session).get_by_type_and_code("user_portal", "staff")
    assert portal is not None, "user_portal:staff must be seeded"
    return await UserRepository(session).add(
        User(
            email=f"upload-{uuid.uuid4().hex[:12]}@test.example.com",
            password_hash=hash_password("Pass1234!"),
            portal_id=portal.id,
            email_verified=True,
        )
    )


def _bearer(user_id: int) -> dict[str, str]:
    token = create_access_token(user_id, extra_claims={"portal": "staff"})
    return {"Authorization": f"Bearer {token}"}


def _pdf_upload() -> dict:
    return {"file": ("test.pdf", b"%PDF-1.4 test", "application/pdf")}


async def test_upload_defaults_entity_type_to_cv(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await _make_user(session)
    res = await client.post(
        "/api/v1/storage/files/upload",
        headers=_bearer(user.id),
        files=_pdf_upload(),
    )
    assert res.status_code == 201
    assert res.json()["entity_type"] == "cv"


async def test_upload_accepts_explicit_entity_type(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await _make_user(session)
    res = await client.post(
        "/api/v1/storage/files/upload",
        headers=_bearer(user.id),
        files=_pdf_upload(),
        data={"entity_type": "avatar"},
    )
    assert res.status_code == 201
    assert res.json()["entity_type"] == "avatar"


async def test_upload_rejects_unknown_entity_type(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await _make_user(session)
    res = await client.post(
        "/api/v1/storage/files/upload",
        headers=_bearer(user.id),
        files=_pdf_upload(),
        data={"entity_type": "malware"},
    )
    assert res.status_code == 422
    assert "entity_type" in res.json()["detail"]
