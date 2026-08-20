"""Creating an application requires a salary expectation.

The candidate portal already blocks an empty field, but that guard lives in the
browser: any caller holding a valid token could POST without it and the row
would land NULL. Talento Humano filters the Kanban by salary range, and an
undeclared expectation cannot be placed inside any range — so the value is
required at the API, which is the door every client has to go through.

The COLUMN stays nullable on purpose: pre-existing rows must not be backfilled
with invented figures, and a future staff-side flow (HR adding a candidate to a
vacancy) may legitimately not know the expectation.
"""

import uuid
from decimal import Decimal

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.security import create_access_token
from app.main import app
from app.modules.auth.application.bootstrap_service import bootstrap_admin
from app.modules.auth.infrastructure.models import User
from app.modules.org.infrastructure.models import (
    ClientCompany,
    Contact,
    Department,
    Parameter,
    Process,
    ProcessStage,
)
from app.modules.org.infrastructure.parameters_repository import ParameterRepository
from app.modules.recruitment.infrastructure.candidate_models import Candidate
from app.modules.recruitment.infrastructure.models import Vacancy
from app.shared.repository import BaseRepository

CREATE_URL = "/api/v1/recruitment/applications"


async def _fixture(session: AsyncSession) -> tuple[Vacancy, Candidate, Parameter]:
    param = await BaseRepository(session, Parameter).add(
        Parameter(type="x", code=uuid.uuid4().hex[:8], name="P")
    )
    company = await BaseRepository(session, ClientCompany).add(ClientCompany(name="Co"))
    contact = await BaseRepository(session, Contact).add(
        Contact(
            client_company_id=company.id,
            first_name="C",
            last_name="D",
            email=f"{uuid.uuid4().hex[:8]}@d.co",
        )
    )
    dept = await BaseRepository(session, Department).add(Department(name="Eng"))
    process = await BaseRepository(session, Process).add(
        Process(
            client_company_id=company.id,
            department_id=dept.id,
            name=f"P{uuid.uuid4().hex[:6]}",
        )
    )
    await BaseRepository(session, ProcessStage).add(
        ProcessStage(process_id=process.id, stage_id=param.id, order=1)
    )
    vacancy = await BaseRepository(session, Vacancy).add(
        Vacancy(
            vacancy_name_id=param.id,
            client_company_id=company.id,
            contact_id=contact.id,
            department_id=dept.id,
            process_id=process.id,
            career_id=param.id,
            city_id=param.id,
            work_mode_id=param.id,
            resource_level_id=param.id,
            status_id=param.id,
        )
    )
    portal = await ParameterRepository(session).get_by_type_and_code("user_portal", "staff")
    assert portal is not None
    user = await BaseRepository(session, User).add(
        User(email=f"{uuid.uuid4().hex[:12]}@test.local", portal_id=portal.id)
    )
    candidate = await BaseRepository(session, Candidate).add(
        Candidate(user_id=user.id, first_name="J", last_name="P")
    )
    await session.flush()
    return vacancy, candidate, param


async def _client_and_headers(session: AsyncSession) -> tuple[AsyncClient, dict[str, str]]:
    admin = await bootstrap_admin(session, f"{uuid.uuid4().hex[:12]}@test.local", "S3cret")
    token = create_access_token(admin.user_id, extra_claims={"portal": "staff"})

    async def _use_test_session():
        yield session

    app.dependency_overrides[get_session] = _use_test_session
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    return client, {"Authorization": f"Bearer {token}"}


async def test_create_without_salary_expectation_is_rejected(session: AsyncSession) -> None:
    vacancy, candidate, param = await _fixture(session)
    client, headers = await _client_and_headers(session)
    try:
        async with client:
            response = await client.post(
                CREATE_URL,
                json={
                    "vacancy_id": vacancy.id,
                    "candidate_id": candidate.id,
                    "status_id": param.id,
                },
                headers=headers,
            )
        assert response.status_code == 422, response.text
    finally:
        app.dependency_overrides.clear()


async def test_create_with_explicit_null_salary_is_rejected(session: AsyncSession) -> None:
    """Sending the key with null must fail too — omission is not the only way in."""
    vacancy, candidate, param = await _fixture(session)
    client, headers = await _client_and_headers(session)
    try:
        async with client:
            response = await client.post(
                CREATE_URL,
                json={
                    "vacancy_id": vacancy.id,
                    "candidate_id": candidate.id,
                    "status_id": param.id,
                    "salary_expectation": None,
                },
                headers=headers,
            )
        assert response.status_code == 422, response.text
    finally:
        app.dependency_overrides.clear()


async def test_create_with_negative_salary_is_rejected(session: AsyncSession) -> None:
    vacancy, candidate, param = await _fixture(session)
    client, headers = await _client_and_headers(session)
    try:
        async with client:
            response = await client.post(
                CREATE_URL,
                json={
                    "vacancy_id": vacancy.id,
                    "candidate_id": candidate.id,
                    "status_id": param.id,
                    "salary_expectation": -1,
                },
                headers=headers,
            )
        assert response.status_code == 422, response.text
    finally:
        app.dependency_overrides.clear()


async def test_create_with_zero_salary_is_accepted(session: AsyncSession) -> None:
    """0 is a declared answer, not a missing one — the candidate owns that choice."""
    vacancy, candidate, param = await _fixture(session)
    client, headers = await _client_and_headers(session)
    try:
        async with client:
            response = await client.post(
                CREATE_URL,
                json={
                    "vacancy_id": vacancy.id,
                    "candidate_id": candidate.id,
                    "status_id": param.id,
                    "salary_expectation": 0,
                },
                headers=headers,
            )
        assert response.status_code == 201, response.text
        assert Decimal(response.json()["salary_expectation"]) == Decimal(0)
    finally:
        app.dependency_overrides.clear()
