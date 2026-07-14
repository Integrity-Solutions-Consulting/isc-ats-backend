"""Slice 2 — GET /interviews/agenda + recruitment.interviews.read_agenda permission.

Covers (design D5/D6):
- 2.1 permission: catalog code exists, Admin auto-holds it, Talento Humano holds it,
  Comercial/Proyecto are excluded, bootstrap is idempotent.
- 2.3/2.4 route: server-side Ecuador-local boundary selection (today + tomorrow),
  cross-owner visibility (R5), enrichment fields, 403 for non-holders / candidate,
  200 for Admin/Talento Humano.
"""

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.security import create_access_token
from app.main import app
from app.modules.auth.application.bootstrap_service import (
    COMERCIAL_PERMISSION_CODES,
    COMERCIAL_ROLE_NAME,
    PROYECTO_PERMISSION_CODES,
    PROYECTO_ROLE_NAME,
    TALENTO_HUMANO_PERMISSION_CODES,
    TALENTO_HUMANO_ROLE_NAME,
    assign_role_to_user,
    bootstrap_admin,
)
from app.modules.auth.infrastructure.authorization_repository import (
    AuthorizationRepository,
)
from app.modules.auth.infrastructure.models import Permission, Role, RolePermission, User
from app.modules.auth.infrastructure.repository import UserRepository
from app.modules.auth.permissions_catalog import ALL_CODES
from app.modules.org.infrastructure.models import (
    ClientCompany,
    Contact,
    Department,
    Parameter,
    Process,
    ProcessStage,
)
from app.modules.org.infrastructure.parameters_repository import ParameterRepository
from app.modules.recruitment.application.slot_generation_service import EC_TZ
from app.modules.recruitment.infrastructure.application_models import Application
from app.modules.recruitment.infrastructure.candidate_models import Candidate
from app.modules.recruitment.infrastructure.interview_models import Interview
from app.modules.recruitment.infrastructure.models import Vacancy
from app.shared.repository import BaseRepository

AGENDA_URL = "/api/v1/recruitment/interviews/agenda"
PERM_CODE = "recruitment.interviews.read_agenda"


# ── 2.1 — permission catalog + bootstrap wiring ────────────────────────────────


def test_read_agenda_permission_in_catalog() -> None:
    assert PERM_CODE in ALL_CODES


def test_talento_humano_holds_read_agenda() -> None:
    assert PERM_CODE in TALENTO_HUMANO_PERMISSION_CODES


def test_comercial_and_proyecto_excluded_from_read_agenda() -> None:
    assert PERM_CODE not in COMERCIAL_PERMISSION_CODES
    assert PERM_CODE not in PROYECTO_PERMISSION_CODES


async def test_admin_holds_read_agenda_after_bootstrap(session: AsyncSession) -> None:
    result = await bootstrap_admin(session, f"admin-{uuid.uuid4().hex[:10]}@test.local", "S3cret")
    codes = await AuthorizationRepository(session).list_permission_codes_for_user(result.user_id)
    assert PERM_CODE in codes


async def test_bootstrap_read_agenda_is_idempotent(session: AsyncSession) -> None:
    email = f"admin-{uuid.uuid4().hex[:10]}@test.local"
    await bootstrap_admin(session, email, "S3cret")
    await bootstrap_admin(session, email, "S3cret")

    role = (
        await session.execute(
            select(Role)
            .where(Role.name == TALENTO_HUMANO_ROLE_NAME)
            .where(Role.is_active.is_(True))
        )
    ).scalar_one()
    granted = set(
        (
            await session.execute(
                select(Permission.code)
                .join(RolePermission, RolePermission.permission_id == Permission.id)
                .where(RolePermission.role_id == role.id)
                .where(RolePermission.is_active.is_(True))
            )
        )
        .scalars()
        .all()
    )
    assert PERM_CODE in granted
    assert granted == set(TALENTO_HUMANO_PERMISSION_CODES)


# ── HTTP fixtures ────────────────────────────────────────────────────────────


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
        User(email=f"{uuid.uuid4().hex[:10]}-{email_suffix}@agenda.local", portal_id=portal.id)
    )


async def _make_candidate_user(session: AsyncSession) -> User:
    portal = await ParameterRepository(session).get_by_type_and_code("user_portal", "candidate")
    assert portal is not None
    return await UserRepository(session).add(
        User(email=f"{uuid.uuid4().hex[:10]}@agenda-candidate.local", portal_id=portal.id)
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


async def _make_agenda_interview(
    session: AsyncSession,
    *,
    interviewer: User,
    scheduled_at_utc: datetime,
    vacancy_name: str,
    candidate_first: str = "Ana",
    candidate_last: str = "Lopez",
    status_code: str | None = None,
) -> Interview:
    """Builds a minimal application/vacancy/candidate graph and a scheduled interview."""
    param_repo = ParameterRepository(session)
    vacancy_name_param = await BaseRepository(session, Parameter).add(
        Parameter(type="vacancy_name", code=uuid.uuid4().hex[:8], name=vacancy_name)
    )
    dummy_param = await BaseRepository(session, Parameter).add(
        Parameter(type="x_agenda_test", code=uuid.uuid4().hex[:8], name="P")
    )
    company = await BaseRepository(session, ClientCompany).add(
        ClientCompany(name=f"Co{uuid.uuid4().hex[:4]}")
    )
    contact = await BaseRepository(session, Contact).add(
        Contact(
            client_company_id=company.id,
            first_name="A",
            last_name="B",
            email=f"{uuid.uuid4().hex[:8]}@agenda.test",
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
    candidate_user = await _make_candidate_user(session)
    candidate = await BaseRepository(session, Candidate).add(
        Candidate(user_id=candidate_user.id, first_name=candidate_first, last_name=candidate_last)
    )
    application = await BaseRepository(session, Application).add(
        Application(vacancy_id=vacancy.id, candidate_id=candidate.id, status_id=dummy_param.id)
    )
    if status_code is not None:
        status_param = await param_repo.get_by_type_and_code("interview_status", status_code)
        assert status_param is not None, f"interview_status:{status_code} must be seeded"
        status_id = status_param.id
    else:
        status_id = dummy_param.id

    return await BaseRepository(session, Interview).add(
        Interview(
            application_id=application.id,
            process_stage_id=stage.id,
            interviewer_id=interviewer.id,
            scheduled_at=scheduled_at_utc,
            ends_at=scheduled_at_utc + timedelta(hours=1),
            status_id=status_id,
            scheduled_by_id=dummy_param.id,
        )
    )


def _ec_today_local_start() -> datetime:
    now_ec = datetime.now(UTC).astimezone(EC_TZ)
    return datetime(now_ec.year, now_ec.month, now_ec.day, tzinfo=EC_TZ)


# ── 2.3/2.4 — route tests ───────────────────────────────────────────────────


async def test_agenda_rejects_missing_token(client: AsyncClient) -> None:
    response = await client.get(AGENDA_URL)
    assert response.status_code == 401


async def test_agenda_forbidden_for_comercial(client: AsyncClient, session: AsyncSession) -> None:
    user = await _user_with_role(session, COMERCIAL_ROLE_NAME, "com")
    response = await client.get(AGENDA_URL, headers=_bearer(user.id))
    assert response.status_code == 403


async def test_agenda_forbidden_for_proyecto(client: AsyncClient, session: AsyncSession) -> None:
    user = await _user_with_role(session, PROYECTO_ROLE_NAME, "proy")
    response = await client.get(AGENDA_URL, headers=_bearer(user.id))
    assert response.status_code == 403


async def test_agenda_forbidden_for_candidate(client: AsyncClient, session: AsyncSession) -> None:
    user = await _make_candidate_user(session)
    response = await client.get(AGENDA_URL, headers=_bearer(user.id, portal="candidate"))
    assert response.status_code == 403


async def test_agenda_allowed_for_admin(client: AsyncClient, session: AsyncSession) -> None:
    admin = await bootstrap_admin(session, f"admin-{uuid.uuid4().hex[:10]}@test.local", "S3cret")
    response = await client.get(AGENDA_URL, headers=_bearer(admin.user_id))
    assert response.status_code == 200
    assert response.json() == []


async def test_agenda_allowed_for_talento_humano(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await _user_with_role(session, TALENTO_HUMANO_ROLE_NAME, "th")
    response = await client.get(AGENDA_URL, headers=_bearer(user.id))
    assert response.status_code == 200


async def test_agenda_shows_todays_interview_from_any_owner(
    client: AsyncClient, session: AsyncSession
) -> None:
    """R5 cross-owner: Admin/TH must see interviews owned by a DIFFERENT interviewer."""
    viewer = await _user_with_role(session, TALENTO_HUMANO_ROLE_NAME, "viewer")
    other_interviewer = await _make_staff_user(session, "other-hr")

    today_local_start = _ec_today_local_start()
    scheduled_at = (today_local_start + timedelta(hours=9)).astimezone(UTC)
    await _make_agenda_interview(
        session,
        interviewer=other_interviewer,
        scheduled_at_utc=scheduled_at,
        vacancy_name="Backend Engineer",
    )

    response = await client.get(AGENDA_URL, headers=_bearer(viewer.id))
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    entry = body[0]
    assert entry["day"] == "today"
    assert entry["candidate_name"] == "Ana Lopez"
    assert entry["vacancy_name"] == "Backend Engineer"
    assert entry["interviewer_email"] == other_interviewer.email


async def test_agenda_buckets_today_and_tomorrow_and_excludes_outside_window(
    client: AsyncClient, session: AsyncSession
) -> None:
    viewer = await _user_with_role(session, TALENTO_HUMANO_ROLE_NAME, "buckets")
    interviewer = await _make_staff_user(session, "buckets-hr")
    today_local_start = _ec_today_local_start()

    # Yesterday 23:59 local — must be excluded.
    await _make_agenda_interview(
        session,
        interviewer=interviewer,
        scheduled_at_utc=(today_local_start - timedelta(minutes=1)).astimezone(UTC),
        vacancy_name="Yesterday Vacancy",
    )
    # Today 09:00 local — bucket "today".
    await _make_agenda_interview(
        session,
        interviewer=interviewer,
        scheduled_at_utc=(today_local_start + timedelta(hours=9)).astimezone(UTC),
        vacancy_name="Today Vacancy",
    )
    # Tomorrow 09:00 local — bucket "tomorrow".
    await _make_agenda_interview(
        session,
        interviewer=interviewer,
        scheduled_at_utc=(today_local_start + timedelta(days=1, hours=9)).astimezone(UTC),
        vacancy_name="Tomorrow Vacancy",
    )
    # Day-after-tomorrow 00:01 local — must be excluded.
    await _make_agenda_interview(
        session,
        interviewer=interviewer,
        scheduled_at_utc=(today_local_start + timedelta(days=2, minutes=1)).astimezone(UTC),
        vacancy_name="Day After Tomorrow Vacancy",
    )

    response = await client.get(AGENDA_URL, headers=_bearer(viewer.id))
    assert response.status_code == 200
    body = response.json()
    names = {e["vacancy_name"]: e["day"] for e in body}
    assert names == {"Today Vacancy": "today", "Tomorrow Vacancy": "tomorrow"}


async def test_agenda_excludes_cancelled_interviews(
    client: AsyncClient, session: AsyncSession
) -> None:
    viewer = await _user_with_role(session, TALENTO_HUMANO_ROLE_NAME, "cancelled")
    interviewer = await _make_staff_user(session, "cancelled-hr")
    today_local_start = _ec_today_local_start()

    await _make_agenda_interview(
        session,
        interviewer=interviewer,
        scheduled_at_utc=(today_local_start + timedelta(hours=10)).astimezone(UTC),
        vacancy_name="Cancelled Vacancy",
        status_code="cancelled",
    )

    response = await client.get(AGENDA_URL, headers=_bearer(viewer.id))
    assert response.status_code == 200
    assert response.json() == []
