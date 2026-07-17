"""GET /interviews/all — global paginated "Entrevistas" list.

Covers:
- Same recruitment.interviews.read_agenda gate as /agenda (403 for Comercial,
  200 for Admin/Talento Humano).
- Pagination (total + page slicing).
- vacancy_id / status_id / date_from / date_to filters.
- Cancelled interviews ARE returned here (unlike /agenda), whether filtered
  explicitly by status_id or left unfiltered.
"""

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.security import create_access_token
from app.main import app
from app.modules.auth.application.bootstrap_service import (
    COMERCIAL_ROLE_NAME,
    TALENTO_HUMANO_ROLE_NAME,
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
    ProcessStage,
)
from app.modules.org.infrastructure.parameters_repository import ParameterRepository
from app.modules.recruitment.infrastructure.application_models import Application
from app.modules.recruitment.infrastructure.candidate_models import Candidate
from app.modules.recruitment.infrastructure.interview_models import Interview
from app.modules.recruitment.infrastructure.models import Vacancy
from app.shared.repository import BaseRepository
from sqlalchemy import select

ALL_URL = "/api/v1/recruitment/interviews/all"


# ── HTTP fixtures (mirrors tests/test_interview_agenda.py) ────────────────────


@pytest.fixture
async def client(session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def _use_test_session() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_session] = _use_test_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


def _bearer(user_id: int, portal: str = "staff") -> dict[str, str]:
    token = create_access_token(user_id, extra_claims={"portal": portal})
    return {"Authorization": f"Bearer {token}"}


async def _make_staff_user(session: AsyncSession, email_suffix: str) -> User:
    portal = await ParameterRepository(session).get_by_type_and_code("user_portal", "staff")
    assert portal is not None
    return await UserRepository(session).add(
        User(email=f"{uuid.uuid4().hex[:10]}-{email_suffix}@all-route.local", portal_id=portal.id)
    )


async def _make_candidate_user(session: AsyncSession) -> User:
    portal = await ParameterRepository(session).get_by_type_and_code("user_portal", "candidate")
    assert portal is not None
    return await UserRepository(session).add(
        User(email=f"{uuid.uuid4().hex[:10]}@all-route-candidate.local", portal_id=portal.id)
    )


async def _user_with_role(session: AsyncSession, role_name: str, email_suffix: str) -> User:
    """Bootstraps the admin (which creates every internal role) then assigns *role_name*."""
    await bootstrap_admin(session, f"admin-{uuid.uuid4().hex[:10]}@test.local", "S3cret")
    user = await _make_staff_user(session, email_suffix)
    role = (
        await session.execute(
            select(Role).where(Role.name == role_name).where(Role.is_active.is_(True))
        )
    ).scalar_one()
    await assign_role_to_user(session, user.id, role.id)
    return user


async def _make_vacancy(session: AsyncSession, *, vacancy_name: str) -> tuple[Vacancy, int, int]:
    """Builds a vacancy graph, returning (vacancy, dummy_param_id, stage_id) for reuse.

    A single ProcessStage is created here and reused by every interview built
    against this vacancy — org.process_stages has UNIQUE(process_id, stage_id)
    and UNIQUE(process_id, order), so minting a fresh stage per interview call
    against the SAME vacancy/process would violate those constraints.
    """
    vacancy_name_param = await BaseRepository(session, Parameter).add(
        Parameter(type="vacancy_name", code=uuid.uuid4().hex[:8], name=vacancy_name)
    )
    dummy_param = await BaseRepository(session, Parameter).add(
        Parameter(type="x_all_route_test", code=uuid.uuid4().hex[:8], name="P")
    )
    company = await BaseRepository(session, ClientCompany).add(
        ClientCompany(name=f"Co{uuid.uuid4().hex[:4]}")
    )
    contact = await BaseRepository(session, Contact).add(
        Contact(
            client_company_id=company.id,
            first_name="A",
            last_name="B",
            email=f"{uuid.uuid4().hex[:8]}@all-route.test",
        )
    )
    dept = await BaseRepository(session, Department).add(Department(name=f"D{uuid.uuid4().hex[:4]}"))
    process = await BaseRepository(session, Process).add(
        Process(client_company_id=company.id, department_id=dept.id, name=f"P{uuid.uuid4().hex[:4]}")
    )
    stage = await BaseRepository(session, ProcessStage).add(
        ProcessStage(process_id=process.id, stage_id=dummy_param.id, order=1)
    )
    vacancy = await BaseRepository(session, Vacancy).add(
        Vacancy(
            vacancy_name_id=vacancy_name_param.id,
            client_company_id=company.id,
            contact_id=contact.id,
            department_id=dept.id,
            process_id=process.id,
            career_id=dummy_param.id,
            city_id=dummy_param.id,
            work_mode_id=dummy_param.id,
            resource_level_id=dummy_param.id,
            status_id=dummy_param.id,
        )
    )
    return vacancy, dummy_param.id, stage.id


async def _make_interview(
    session: AsyncSession,
    *,
    interviewer: User,
    vacancy: Vacancy,
    dummy_param_id: int,
    stage_id: int,
    scheduled_at_utc: datetime,
    candidate_first: str = "Ana",
    candidate_last: str = "Lopez",
    status_code: str = "scheduled",
) -> Interview:
    """Builds a candidate/application and a scheduled interview against *vacancy*."""
    param_repo = ParameterRepository(session)
    candidate_user = await _make_candidate_user(session)
    candidate = await BaseRepository(session, Candidate).add(
        Candidate(user_id=candidate_user.id, first_name=candidate_first, last_name=candidate_last)
    )
    application = await BaseRepository(session, Application).add(
        Application(vacancy_id=vacancy.id, candidate_id=candidate.id, status_id=dummy_param_id)
    )
    status_param = await param_repo.get_by_type_and_code("interview_status", status_code)
    assert status_param is not None, f"interview_status:{status_code} must be seeded"

    return await BaseRepository(session, Interview).add(
        Interview(
            application_id=application.id,
            process_stage_id=stage_id,
            interviewer_id=interviewer.id,
            scheduled_at=scheduled_at_utc,
            ends_at=scheduled_at_utc + timedelta(hours=1),
            status_id=status_param.id,
            scheduled_by_id=dummy_param_id,
        )
    )


# ── Permission gate ────────────────────────────────────────────────────────────


async def test_all_rejects_missing_token(client: AsyncClient) -> None:
    response = await client.get(ALL_URL)
    assert response.status_code == 401


async def test_all_forbidden_for_comercial(client: AsyncClient, session: AsyncSession) -> None:
    user = await _user_with_role(session, COMERCIAL_ROLE_NAME, "com")
    response = await client.get(ALL_URL, headers=_bearer(user.id))
    assert response.status_code == 403


async def test_all_allowed_for_admin(client: AsyncClient, session: AsyncSession) -> None:
    """200 + a well-formed Page envelope.

    Note: this hits the real local dev database (per tests/conftest.py), which
    already carries committed dev-seed interviews (scripts/seed_dev_data.py) —
    unlike /agenda (date-windowed) there is no query-side scoping that hides
    them here, so we can't assert an empty list. Scoped assertions (by
    vacancy_id/date range) live in the dedicated filter tests below.
    """
    admin = await bootstrap_admin(session, f"admin-{uuid.uuid4().hex[:10]}@test.local", "S3cret")
    response = await client.get(ALL_URL, headers=_bearer(admin.user_id))
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["items"], list)
    assert body["total"] >= len(body["items"])
    assert body["page"] == 1
    assert body["size"] == 20


async def test_all_allowed_for_talento_humano(client: AsyncClient, session: AsyncSession) -> None:
    user = await _user_with_role(session, TALENTO_HUMANO_ROLE_NAME, "th")
    response = await client.get(ALL_URL, headers=_bearer(user.id))
    assert response.status_code == 200


# ── Pagination + filters ───────────────────────────────────────────────────────


async def test_all_pagination_slices_and_reports_total(
    client: AsyncClient, session: AsyncSession
) -> None:
    viewer = await _user_with_role(session, TALENTO_HUMANO_ROLE_NAME, "pag")
    interviewer = await _make_staff_user(session, "pag-hr")
    vacancy, dummy_id, stage_id = await _make_vacancy(session, vacancy_name="Pagination Vacancy")
    base = datetime.now(UTC).replace(microsecond=0)

    for i in range(3):
        await _make_interview(
            session,
            interviewer=interviewer,
            vacancy=vacancy,
            dummy_param_id=dummy_id,
            stage_id=stage_id,
            scheduled_at_utc=base + timedelta(hours=i),
        )

    # Scope to this test's own vacancy — the local dev DB already carries
    # committed dev-seed interviews (see test_all_allowed_for_admin note).
    response = await client.get(
        ALL_URL,
        headers=_bearer(viewer.id),
        params={"page": 1, "size": 2, "vacancy_id": vacancy.id},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2
    assert body["page"] == 1
    assert body["size"] == 2

    response2 = await client.get(
        ALL_URL,
        headers=_bearer(viewer.id),
        params={"page": 2, "size": 2, "vacancy_id": vacancy.id},
    )
    body2 = response2.json()
    assert len(body2["items"]) == 1


async def test_all_filters_by_vacancy_id(client: AsyncClient, session: AsyncSession) -> None:
    viewer = await _user_with_role(session, TALENTO_HUMANO_ROLE_NAME, "vac")
    interviewer = await _make_staff_user(session, "vac-hr")
    vacancy_a, dummy_a, stage_a = await _make_vacancy(session, vacancy_name="Vacancy A")
    vacancy_b, dummy_b, stage_b = await _make_vacancy(session, vacancy_name="Vacancy B")
    base = datetime.now(UTC).replace(microsecond=0)

    await _make_interview(
        session,
        interviewer=interviewer,
        vacancy=vacancy_a,
        dummy_param_id=dummy_a,
        stage_id=stage_a,
        scheduled_at_utc=base,
    )
    await _make_interview(
        session,
        interviewer=interviewer,
        vacancy=vacancy_b,
        dummy_param_id=dummy_b,
        stage_id=stage_b,
        scheduled_at_utc=base + timedelta(hours=1),
    )

    response = await client.get(
        ALL_URL, headers=_bearer(viewer.id), params={"vacancy_id": vacancy_a.id}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["vacancy_id"] == vacancy_a.id
    assert body["items"][0]["vacancy_name"] == "Vacancy A"


async def test_all_filters_by_status_id_and_includes_cancelled(
    client: AsyncClient, session: AsyncSession
) -> None:
    viewer = await _user_with_role(session, TALENTO_HUMANO_ROLE_NAME, "status")
    interviewer = await _make_staff_user(session, "status-hr")
    vacancy, dummy_id, stage_id = await _make_vacancy(session, vacancy_name="Status Vacancy")
    base = datetime.now(UTC).replace(microsecond=0)

    await _make_interview(
        session,
        interviewer=interviewer,
        vacancy=vacancy,
        dummy_param_id=dummy_id,
        stage_id=stage_id,
        scheduled_at_utc=base,
        status_code="scheduled",
    )
    await _make_interview(
        session,
        interviewer=interviewer,
        vacancy=vacancy,
        dummy_param_id=dummy_id,
        stage_id=stage_id,
        scheduled_at_utc=base + timedelta(hours=1),
        status_code="cancelled",
    )

    # Unfiltered (by status): cancelled interview IS included (unlike /agenda).
    # Scoped by vacancy_id so pre-existing dev-seed interviews don't interfere.
    response_all = await client.get(
        ALL_URL, headers=_bearer(viewer.id), params={"vacancy_id": vacancy.id}
    )
    assert response_all.status_code == 200
    body_all = response_all.json()
    assert body_all["total"] == 2
    statuses = {item["status"] for item in body_all["items"]}
    assert statuses == {"scheduled", "cancelled"}

    cancelled_param = await ParameterRepository(session).get_by_type_and_code(
        "interview_status", "cancelled"
    )
    assert cancelled_param is not None

    response_cancelled = await client.get(
        ALL_URL,
        headers=_bearer(viewer.id),
        params={"status_id": cancelled_param.id, "vacancy_id": vacancy.id},
    )
    body_cancelled = response_cancelled.json()
    assert body_cancelled["total"] == 1
    assert body_cancelled["items"][0]["status"] == "cancelled"


async def test_all_filters_by_date_range(client: AsyncClient, session: AsyncSession) -> None:
    viewer = await _user_with_role(session, TALENTO_HUMANO_ROLE_NAME, "date")
    interviewer = await _make_staff_user(session, "date-hr")
    vacancy, dummy_id, stage_id = await _make_vacancy(session, vacancy_name="Date Vacancy")
    base = datetime(2026, 1, 1, tzinfo=UTC)

    await _make_interview(
        session,
        interviewer=interviewer,
        vacancy=vacancy,
        dummy_param_id=dummy_id,
        stage_id=stage_id,
        scheduled_at_utc=base,
        candidate_first="Before",
    )
    await _make_interview(
        session,
        interviewer=interviewer,
        vacancy=vacancy,
        dummy_param_id=dummy_id,
        stage_id=stage_id,
        scheduled_at_utc=base + timedelta(days=5),
        candidate_first="Inside",
    )
    await _make_interview(
        session,
        interviewer=interviewer,
        vacancy=vacancy,
        dummy_param_id=dummy_id,
        stage_id=stage_id,
        scheduled_at_utc=base + timedelta(days=20),
        candidate_first="After",
    )

    response = await client.get(
        ALL_URL,
        headers=_bearer(viewer.id),
        params={
            "date_from": (base + timedelta(days=1)).isoformat(),
            "date_to": (base + timedelta(days=10)).isoformat(),
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["candidate_name"] == "Inside Lopez"


async def test_all_includes_open_offer_with_null_scheduled_at(
    client: AsyncClient, session: AsyncSession
) -> None:
    """A Mode B offer HR just sent (status=offered) has no scheduled_at yet — the
    candidate hasn't picked a slot. It must still show up here (unlike /agenda,
    which only cares about interviews with a real time), with scheduled_at=null
    and status="offered" in the payload."""
    viewer = await _user_with_role(session, TALENTO_HUMANO_ROLE_NAME, "offer")
    interviewer = await _make_staff_user(session, "offer-hr")
    vacancy, dummy_id, stage_id = await _make_vacancy(session, vacancy_name="Offer Vacancy")

    candidate_user = await _make_candidate_user(session)
    candidate = await BaseRepository(session, Candidate).add(
        Candidate(user_id=candidate_user.id, first_name="Pending", last_name="Offer")
    )
    application = await BaseRepository(session, Application).add(
        Application(vacancy_id=vacancy.id, candidate_id=candidate.id, status_id=dummy_id)
    )
    offered_param = await ParameterRepository(session).get_by_type_and_code(
        "interview_status", "offered"
    )
    assert offered_param is not None
    await BaseRepository(session, Interview).add(
        Interview(
            application_id=application.id,
            process_stage_id=stage_id,
            interviewer_id=interviewer.id,
            scheduled_at=None,
            ends_at=None,
            status_id=offered_param.id,
            scheduled_by_id=dummy_id,
        )
    )

    response = await client.get(
        ALL_URL, headers=_bearer(viewer.id), params={"vacancy_id": vacancy.id}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["status"] == "offered"
    assert body["items"][0]["scheduled_at"] is None
