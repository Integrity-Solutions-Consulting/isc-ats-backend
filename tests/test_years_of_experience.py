"""Tests for candidates.years_of_experience — a candidate-portal profile field
(like phone/current_company), NOT an application-time field.

Covers:
- The schema accepts a decimal value and rejects a negative one.
- PATCH /recruitment/candidates/{id} can set and clear the field.
- A candidate-portal self-apply is blocked with 422 while the field is unset on
  their own profile, and unblocked once they set it.
- Staff creating an application on a candidate's behalf are exempt from the gate.
- The expanded read (both the repository query and the /candidates/expanded
  endpoint the frontend profile page reads from) surfaces the new column.
"""

import uuid
from collections.abc import AsyncGenerator
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.security import create_access_token
from app.main import app
from app.modules.auth.application.bootstrap_service import (
    CANDIDATE_ROLE_NAME,
    assign_role_to_user,
    bootstrap_admin,
)
from app.modules.auth.infrastructure.models import Role, User
from app.modules.auth.infrastructure.repository import UserRepository
from app.modules.org.infrastructure.models import (
    ClientCompany,
    Contact,
    Department,
    Parameter,
    Process,
)
from app.modules.org.infrastructure.parameters_repository import ParameterRepository
from app.modules.recruitment.api.candidates_schemas import CandidateCreate
from app.modules.recruitment.infrastructure.candidate_models import Candidate
from app.modules.recruitment.infrastructure.candidates_expanded import (
    CandidatesExpandedRepository,
)
from app.modules.recruitment.infrastructure.models import Vacancy
from app.shared.repository import BaseRepository

CANDIDATES_URL = "/api/v1/recruitment/candidates"
APPLICATIONS_URL = "/api/v1/recruitment/applications"
BLOCK_MESSAGE = "Debes completar tus años de experiencia en tu perfil antes de postular."


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


async def _candidate_with_role(session: AsyncSession) -> tuple[User, Candidate]:
    """A candidate-portal user holding the bootstrapped candidate role + row."""
    portal = await ParameterRepository(session).get_by_type_and_code(
        "user_portal", "candidate"
    )
    assert portal is not None, "user_portal:candidate must be seeded"
    user = await UserRepository(session).add(
        User(email=f"{uuid.uuid4().hex[:12]}@cand.local", portal_id=portal.id)
    )
    role = (
        await session.execute(
            select(Role)
            .where(Role.name == CANDIDATE_ROLE_NAME)
            .where(Role.is_active.is_(True))
        )
    ).scalar_one()
    await assign_role_to_user(session, user.id, role.id)
    candidate = await BaseRepository(session, Candidate).add(
        Candidate(user_id=user.id, first_name="Test", last_name="Candidate")
    )
    return user, candidate


async def _active_status_param(session: AsyncSession) -> Parameter:
    """The seeded vacancy_status 'active' param, or a freshly created one — a
    candidate-portal caller may only apply to a PUBLISHED vacancy."""
    repo = ParameterRepository(session)
    existing = await repo.get_by_type_and_code("vacancy_status", "active")
    if existing is not None:
        return existing
    return await BaseRepository(session, Parameter).add(
        Parameter(type="vacancy_status", code="active", name="Activa")
    )


async def _vacancy_graph(session: AsyncSession, *, published: bool) -> tuple[Vacancy, Parameter]:
    """A minimal persisted vacancy + reusable parameter (for status FKs)."""
    param = await BaseRepository(session, Parameter).add(
        Parameter(type="x", code=uuid.uuid4().hex[:8], name="P")
    )
    status_id = (await _active_status_param(session)).id if published else param.id
    company = await BaseRepository(session, ClientCompany).add(ClientCompany(name="ACME"))
    contact = await BaseRepository(session, Contact).add(
        Contact(client_company_id=company.id, first_name="A", last_name="B", email="a@b.co")
    )
    dept = await BaseRepository(session, Department).add(Department(name="Tech"))
    process = await BaseRepository(session, Process).add(
        Process(
            client_company_id=company.id,
            department_id=dept.id,
            name=f"P{uuid.uuid4().hex[:6]}",
        )
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
            status_id=status_id,
        )
    )
    return vacancy, param


def _application_payload(vacancy: Vacancy, candidate: Candidate, param: Parameter) -> dict:
    return {
        "vacancy_id": vacancy.id,
        "candidate_id": candidate.id,
        "status_id": param.id,
        "salary_expectation": 1200,
    }


# ── Schema-level: accepts decimal, rejects negative ────────────────────────────


def test_years_of_experience_accepts_decimal_value() -> None:
    data = CandidateCreate(
        user_id=1, first_name="J", last_name="P", years_of_experience=Decimal("2.5")
    )
    assert data.years_of_experience == Decimal("2.5")


def test_years_of_experience_rejects_negative() -> None:
    with pytest.raises(ValidationError):
        CandidateCreate(
            user_id=1, first_name="J", last_name="P", years_of_experience=Decimal("-1")
        )


# ── PATCH sets and clears the field ─────────────────────────────────────────────


async def test_patch_candidate_can_set_years_of_experience(
    client: AsyncClient, session: AsyncSession
) -> None:
    user, candidate = await _candidate_with_role(session)

    response = await client.patch(
        f"{CANDIDATES_URL}/{candidate.id}",
        json={"years_of_experience": 3.5},
        headers=_bearer(user.id),
    )

    assert response.status_code == 200, response.text
    assert Decimal(str(response.json()["years_of_experience"])) == Decimal("3.5")


async def test_patch_candidate_can_clear_years_of_experience(
    client: AsyncClient, session: AsyncSession
) -> None:
    user, candidate = await _candidate_with_role(session)
    candidate.years_of_experience = Decimal("4.0")
    await session.flush()

    response = await client.patch(
        f"{CANDIDATES_URL}/{candidate.id}",
        json={"years_of_experience": None},
        headers=_bearer(user.id),
    )

    assert response.status_code == 200, response.text
    assert response.json()["years_of_experience"] is None


# ── Apply-blocking validation (candidate portal only) ───────────────────────────


async def test_candidate_portal_blocked_from_applying_without_years_of_experience(
    client: AsyncClient, session: AsyncSession
) -> None:
    user, candidate = await _candidate_with_role(session)
    vacancy, param = await _vacancy_graph(session, published=True)

    response = await client.post(
        APPLICATIONS_URL,
        json=_application_payload(vacancy, candidate, param),
        headers=_bearer(user.id),
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"] == BLOCK_MESSAGE


async def test_candidate_portal_can_apply_after_setting_years_of_experience(
    client: AsyncClient, session: AsyncSession
) -> None:
    user, candidate = await _candidate_with_role(session)
    candidate.years_of_experience = Decimal("1.5")
    await session.flush()
    vacancy, param = await _vacancy_graph(session, published=True)

    response = await client.post(
        APPLICATIONS_URL,
        json=_application_payload(vacancy, candidate, param),
        headers=_bearer(user.id),
    )

    assert response.status_code == 201, response.text


async def test_staff_created_application_is_exempt_from_the_gate(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Staff placing a candidate into a vacancy manually must not be blocked by
    the candidate's own missing years_of_experience — the gate is a self-apply
    guard, not a data-completeness gate on the candidate row in general."""
    admin = await bootstrap_admin(session, f"{uuid.uuid4().hex[:12]}@test.local", "S3cret")
    _user, candidate = await _candidate_with_role(session)
    assert candidate.years_of_experience is None
    vacancy, param = await _vacancy_graph(session, published=False)

    response = await client.post(
        APPLICATIONS_URL,
        json=_application_payload(vacancy, candidate, param),
        headers=_bearer(admin.user_id, portal="staff"),
    )

    assert response.status_code == 201, response.text


# ── Expanded read surfaces the new column ───────────────────────────────────────


async def test_candidates_expanded_repository_includes_years_of_experience(
    session: AsyncSession,
) -> None:
    _user, candidate = await _candidate_with_role(session)
    candidate.years_of_experience = Decimal("6.0")
    await session.flush()

    repo = CandidatesExpandedRepository(session)
    expanded = await repo.get_expanded(candidate.id)

    assert expanded is not None
    assert expanded.years_of_experience == Decimal("6.0")


async def test_candidates_expanded_endpoint_includes_years_of_experience(
    client: AsyncClient, session: AsyncSession
) -> None:
    """This is the endpoint the frontend candidate profile page reads from."""
    user, candidate = await _candidate_with_role(session)
    candidate.years_of_experience = Decimal("2.0")
    await session.flush()

    response = await client.get(
        f"{CANDIDATES_URL}/expanded",
        headers=_bearer(user.id),
    )

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert len(items) == 1
    assert Decimal(str(items[0]["years_of_experience"])) == Decimal("2.0")
