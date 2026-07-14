"""Tests for POST /recruitment/vacancies/{id}/generate-poster (Task 1.1).

The endpoint used to be an idempotent GET that streamed raw PNG bytes with no
persistence. It is now a side-effecting POST gated on
``ai.vacancy_promo_images.create``: it generates the poster, uploads it to
MinIO, and persists a storage.files row + an ai.vacancy_promo_images row.

Poster rendering itself (PyMuPDF compositing, tested in test_poster_generator.py)
is monkeypatched out here — this file focuses on the RBAC gate and the new
persistence side effects, not image-generation depth.
"""

import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.security import create_access_token
from app.main import app
from app.modules.ai.infrastructure.models import VacancyPromoImage
from app.modules.auth.application.bootstrap_service import (
    COMERCIAL_ROLE_NAME,
    TALENTO_HUMANO_ROLE_NAME,
    assign_role_to_user,
    bootstrap_admin,
)
from app.modules.auth.infrastructure.models import Role, User
from app.modules.auth.infrastructure.repository import UserRepository
from app.modules.org.infrastructure.models import ClientCompany, Contact, Department, Parameter
from app.modules.org.infrastructure.parameters_repository import ParameterRepository
from app.modules.recruitment.api import vacancies_routes
from app.modules.recruitment.infrastructure.models import Vacancy
from app.modules.storage.infrastructure.models import File
from app.shared.repository import BaseRepository

BASE = "/api/v1/recruitment/vacancies"


@pytest.fixture
async def client(session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def _use_test_session() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_session] = _use_test_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


def _bearer(user_id: int) -> dict[str, str]:
    token = create_access_token(user_id, extra_claims={"portal": "staff"})
    return {"Authorization": f"Bearer {token}"}


async def _build_vacancy(session: AsyncSession) -> Vacancy:
    """Minimal persisted Vacancy row — enough for FK validation, no business rules."""
    p = await BaseRepository(session, Parameter).add(
        Parameter(type="x", code=uuid.uuid4().hex[:8], name="Param")
    )
    company = await BaseRepository(session, ClientCompany).add(
        ClientCompany(name=f"Co{uuid.uuid4().hex[:6]}")
    )
    contact = await BaseRepository(session, Contact).add(
        Contact(
            client_company_id=company.id,
            first_name="A",
            last_name="B",
            email=f"a{uuid.uuid4().hex[:6]}@test.co",
        )
    )
    dept = await BaseRepository(session, Department).add(
        Department(name=f"D{uuid.uuid4().hex[:6]}")
    )
    return await BaseRepository(session, Vacancy).add(
        Vacancy(
            vacancy_name_id=p.id,
            client_company_id=company.id,
            contact_id=contact.id,
            department_id=dept.id,
            career_id=p.id,
            city_id=p.id,
            work_mode_id=p.id,
            resource_level_id=p.id,
            status_id=p.id,
        )
    )


async def _staff_user_with_role(session: AsyncSession, role_name: str) -> int:
    portal = await ParameterRepository(session).get_by_type_and_code("user_portal", "staff")
    assert portal is not None
    user = await UserRepository(session).add(
        User(email=f"{uuid.uuid4().hex[:12]}@staff.local", portal_id=portal.id)
    )
    role = (
        await session.execute(
            select(Role).where(Role.name == role_name).where(Role.is_active.is_(True))
        )
    ).scalar_one()
    await assign_role_to_user(session, user.id, role.id)
    await session.flush()
    return user.id


async def _fake_generate_vacancy_poster(vacancy_id: int) -> bytes:
    return b"fake-poster-bytes-for-test"


async def test_comercial_forbidden_from_generating_poster(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Comercial holds ai.vacancy_promo_images.read only — POST (create) must 403.

    require_permission runs as a route dependency, before the handler body, so
    the permission gate rejects the request before the vacancy is even looked
    up — a non-existent vacancy_id is fine here.
    """
    await bootstrap_admin(session, f"{uuid.uuid4().hex[:12]}@test.local", "S3cret")
    user_id = await _staff_user_with_role(session, COMERCIAL_ROLE_NAME)

    res = await client.post(f"{BASE}/999999/generate-poster", headers=_bearer(user_id))
    assert res.status_code == 403, res.text


async def test_talento_humano_generates_and_persists_poster(
    client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TH holds ai.vacancy_promo_images.create — POST succeeds and persists a
    File row + a VacancyPromoImage row referencing it."""
    monkeypatch.setattr(
        vacancies_routes, "generate_vacancy_poster", _fake_generate_vacancy_poster
    )

    await bootstrap_admin(session, f"{uuid.uuid4().hex[:12]}@test.local", "S3cret")
    user_id = await _staff_user_with_role(session, TALENTO_HUMANO_ROLE_NAME)
    vacancy = await _build_vacancy(session)
    await session.flush()

    res = await client.post(f"{BASE}/{vacancy.id}/generate-poster", headers=_bearer(user_id))
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["vacancy_id"] == vacancy.id
    assert body["is_active"] is True

    file_row = await BaseRepository(session, File).get(body["file_id"])
    assert file_row is not None
    assert file_row.entity_type == "vacancy_image"
    assert file_row.entity_id == vacancy.id

    promo_row = await BaseRepository(session, VacancyPromoImage).get(body["id"])
    assert promo_row is not None
    assert promo_row.vacancy_id == vacancy.id
    assert promo_row.file_id == file_row.id
