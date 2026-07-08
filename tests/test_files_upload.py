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
    # A real PNG — avatars are opened and downscaled by Pillow at upload time.
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (600, 400), (100, 150, 200)).save(buf, format="PNG")
    return {"file": ("avatar.png", buf.getvalue(), "image/png")}


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
    # A body over the CV cap (5 MiB) → 413, without reaching MinIO. The cap is
    # per entity_type and applies to staff and candidate uploads alike.
    from app.modules.storage.application.upload_validation import max_bytes_for

    user = await _make_user(session)
    oversized = b"%PDF-1.4" + b"\x00" * (max_bytes_for("cv") + 1)
    res = await client.post(
        "/api/v1/storage/files/upload",
        headers=_bearer(user.id),
        files={"file": ("big.pdf", oversized, "application/pdf")},
        data={"entity_type": "cv"},
    )
    assert res.status_code == 413


async def test_upload_avatar_rejects_over_its_lower_limit(
    client: AsyncClient, session: AsyncSession
) -> None:
    # Avatars have a lower cap (5 MiB) than the 10 MiB global. A payload above the
    # avatar cap is a 413, even though it would fit a CV. Rejected on size before
    # the image is ever decoded, so a fake PNG body is fine here.
    from app.modules.storage.application.upload_validation import max_bytes_for

    user = await _make_user(session)
    oversized = b"\x89PNG\r\n\x1a\n" + b"\x00" * (max_bytes_for("avatar") + 1)
    res = await client.post(
        "/api/v1/storage/files/upload",
        headers=_bearer(user.id),
        files={"file": ("big.png", oversized, "image/png")},
        data={"entity_type": "avatar"},
    )
    assert res.status_code == 413


async def test_upload_rejects_candidate_for_staff_entity_type(
    client: AsyncClient, session: AsyncSession
) -> None:
    # Candidates may only upload their own CVs and avatars; staff-only types
    # (vacancy_image, word_doc) are rejected for candidate-portal tokens.
    user = await _make_user(session)
    candidate_token = create_access_token(user.id, extra_claims={"portal": "candidate"})

    res = await client.post(
        "/api/v1/storage/files/upload",
        headers={"Authorization": f"Bearer {candidate_token}"},
        files=_pdf_upload(),
        data={"entity_type": "vacancy_image"},
    )
    assert res.status_code == 403
    assert "restricted" in res.json()["detail"].lower()


# ── Storage/RAM DoS defenses: CV cap 5 MiB + per-account CV quota ──────────────

def _candidate_bearer(user_id: int) -> dict[str, str]:
    token = create_access_token(user_id, extra_claims={"portal": "candidate"})
    return {"Authorization": f"Bearer {token}"}


async def _seed_cv_files(
    session: AsyncSession, user_id: int, count: int, size_bytes: int
) -> None:
    from app.core.config import settings
    from app.modules.storage.infrastructure.models import File
    from app.shared.repository import BaseRepository

    for _ in range(count):
        await BaseRepository(session, File).add(
            File(
                original_name="cv.pdf",
                stored_key=uuid.uuid4().hex,
                bucket=settings.minio_bucket,
                mime_type="application/pdf",
                size_bytes=size_bytes,
                is_public=False,
                entity_type="cv",
                created_by=user_id,
            )
        )


async def test_cv_upload_cap_is_5_mib(
    client: AsyncClient, session: AsyncSession
) -> None:
    """The CV cap is lowered from the 10 MiB global to 5 MiB — a 5 MiB+1 CV is 413."""
    from app.modules.storage.application.upload_validation import max_bytes_for

    assert max_bytes_for("cv") == 5 * 1024 * 1024
    user = await _make_user(session)
    oversized = b"%PDF-1.4" + b"\x00" * (max_bytes_for("cv") + 1)
    res = await client.post(
        "/api/v1/storage/files/upload",
        headers=_candidate_bearer(user.id),
        files={"file": ("big.pdf", oversized, "application/pdf")},
        data={"entity_type": "cv"},
    )
    assert res.status_code == 413


async def test_cv_upload_rejected_when_count_quota_exceeded(
    client: AsyncClient, session: AsyncSession
) -> None:
    from app.core.config import settings

    user = await _make_user(session)
    await _seed_cv_files(session, user.id, settings.cv_max_active_per_user, 1000)
    res = await client.post(
        "/api/v1/storage/files/upload",
        headers=_candidate_bearer(user.id),
        files=_pdf_upload(),
        data={"entity_type": "cv"},
    )
    assert res.status_code == 429


async def test_cv_upload_rejected_when_byte_quota_exceeded(
    client: AsyncClient, session: AsyncSession
) -> None:
    from app.core.config import settings

    user = await _make_user(session)
    await _seed_cv_files(session, user.id, 1, settings.cv_max_total_bytes_per_user)
    res = await client.post(
        "/api/v1/storage/files/upload",
        headers=_candidate_bearer(user.id),
        files=_pdf_upload(),
        data={"entity_type": "cv"},
    )
    assert res.status_code == 429


async def test_cv_upload_allowed_under_quota(
    client: AsyncClient, session: AsyncSession
) -> None:
    """A candidate under quota uploads a CV normally (end-to-end through MinIO)."""
    user = await _make_user(session)
    res = await client.post(
        "/api/v1/storage/files/upload",
        headers=_candidate_bearer(user.id),
        files=_pdf_upload(),
        data={"entity_type": "cv"},
    )
    assert res.status_code == 201
    assert res.json()["entity_type"] == "cv"
