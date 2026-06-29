"""Tests for POST /storage/files/upload — entity_type handling.

Covers:
- entity_type is REQUIRED — a missing field is a 422, never a silent default.
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


def _png_upload() -> dict:
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    return {"file": ("avatar.png", png, "image/png")}


async def test_upload_requires_entity_type(
    client: AsyncClient, session: AsyncSession
) -> None:
    # entity_type is mandatory: omitting it is a validation error, never a silent
    # default. A wrong default would mislabel files and reject valid ones (e.g. an
    # avatar validated against the cv (pdf-only) allowlist).
    user = await _make_user(session)
    res = await client.post(
        "/api/v1/storage/files/upload",
        headers=_bearer(user.id),
        files=_pdf_upload(),
    )
    assert res.status_code == 422
    assert "entity_type" in res.text


async def test_upload_accepts_explicit_entity_type(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await _make_user(session)
    res = await client.post(
        "/api/v1/storage/files/upload",
        headers=_bearer(user.id),
        files=_png_upload(),
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


async def test_upload_rejects_content_type_mismatch(
    client: AsyncClient, session: AsyncSession
) -> None:
    # A non-PDF payload tagged as a CV is rejected by magic-byte inspection,
    # before anything is sent to object storage.
    user = await _make_user(session)
    res = await client.post(
        "/api/v1/storage/files/upload",
        headers=_bearer(user.id),
        files={"file": ("cv.pdf", b"MZ\x90\x00not-a-pdf", "application/pdf")},
        data={"entity_type": "cv"},
    )
    assert res.status_code == 422


async def test_upload_rejects_oversized_payload(
    client: AsyncClient, session: AsyncSession
) -> None:
    # An 11 MiB body exceeds the 10 MiB cap → 413, without reaching MinIO.
    from app.modules.storage.application.upload_validation import MAX_UPLOAD_BYTES

    user = await _make_user(session)
    oversized = b"%PDF-1.4" + b"\x00" * (MAX_UPLOAD_BYTES + 1)
    res = await client.post(
        "/api/v1/storage/files/upload",
        headers=_bearer(user.id),
        files={"file": ("big.pdf", oversized, "application/pdf")},
        data={"entity_type": "cv"},
    )
    assert res.status_code == 413
