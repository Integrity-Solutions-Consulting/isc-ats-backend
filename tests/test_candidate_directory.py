import uuid
from collections.abc import AsyncGenerator
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.dependencies import CurrentUser
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
from app.modules.recruitment.infrastructure.application_models import Application
from app.modules.recruitment.infrastructure.candidate_models import Candidate
from app.modules.recruitment.infrastructure.candidates_expanded import (
    CandidatesExpandedRepository,
)
from app.modules.recruitment.infrastructure.models import Vacancy
from app.modules.talent.infrastructure.models import TalentPool
from app.shared.pagination import PageParams
from app.shared.repository import BaseRepository

ACTOR = CurrentUser(user_id=1, ip="127.0.0.1")


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


async def _make_candidate(
    session: AsyncSession,
    *,
    first_name: str = "Ana",
    last_name: str = "Diaz",
    email: str | None = None,
    skills: list[str] | None = None,
    tools: list[str] | None = None,
    career_id: int | None = None,
) -> Candidate:
    portal = await ParameterRepository(session).get_by_type_and_code("user_portal", "staff")
    assert portal is not None
    user_email = email or f"{uuid.uuid4().hex[:12]}@test.local"
    user = await BaseRepository(session, User).add(User(email=user_email, portal_id=portal.id))
    parsed_data = {}
    if skills:
        parsed_data["skills"] = skills
    if tools:
        parsed_data["tools"] = tools

    return await BaseRepository(session, Candidate).add(
        Candidate(
            user_id=user.id,
            first_name=first_name,
            last_name=last_name,
            career_id=career_id,
            parsed_data=parsed_data or None,
        )
    )


async def _make_vacancy(session: AsyncSession, title_name: str = "Backend Engineer") -> Vacancy:
    param_type = f"p_{uuid.uuid4().hex[:6]}"
    title_param = await BaseRepository(session, Parameter).add(
        Parameter(type="vacancy_name", code=uuid.uuid4().hex[:8], name=title_name)
    )
    other_param = await BaseRepository(session, Parameter).add(
        Parameter(type=param_type, code=uuid.uuid4().hex[:8], name="Param")
    )
    company = await BaseRepository(session, ClientCompany).add(ClientCompany(name="ACME Corp"))
    contact = await BaseRepository(session, Contact).add(
        Contact(
            client_company_id=company.id,
            first_name="Recruiter",
            last_name="Test",
            email=f"{uuid.uuid4().hex[:6]}@acme.com",
        )
    )
    dept = await BaseRepository(session, Department).add(Department(name="Engineering"))
    process = await BaseRepository(session, Process).add(
        Process(
            client_company_id=company.id,
            department_id=dept.id,
            name=f"Process {uuid.uuid4().hex[:6]}",
        )
    )
    return await BaseRepository(session, Vacancy).add(
        Vacancy(
            vacancy_name_id=title_param.id,
            client_company_id=company.id,
            contact_id=contact.id,
            department_id=dept.id,
            process_id=process.id,
            career_id=other_param.id,
            city_id=other_param.id,
            work_mode_id=other_param.id,
            resource_level_id=other_param.id,
            status_id=other_param.id,
            created_by=1,
        )
    )


async def _apply_to_vacancy(
    session: AsyncSession,
    candidate_id: int,
    vacancy_id: int,
    *,
    match_score: Decimal | None = Decimal("88.50"),
) -> Application:
    status_param = await BaseRepository(session, Parameter).add(
        Parameter(type="application_status", code=uuid.uuid4().hex[:8], name="Postulado")
    )
    return await BaseRepository(session, Application).add(
        Application(
            candidate_id=candidate_id,
            vacancy_id=vacancy_id,
            status_id=status_param.id,
            match_score=match_score,
            created_by=1,
        )
    )


async def test_list_directory_all_candidates_and_talent_pool_status(session: AsyncSession) -> None:
    """Pins that list_directory returns all registered candidates, whether in talent pool or not,
    with exact talent pool membership metadata and without row duplication."""
    c1 = await _make_candidate(session, first_name="CandidatoUno", last_name="EnPool")
    c2 = await _make_candidate(session, first_name="CandidatoDos", last_name="FueraPool")

    # Add c1 to talent pool
    tp_repo = BaseRepository(session, TalentPool)
    tp_entry = await tp_repo.add(
        TalentPool(candidate_id=c1.id, is_active=True, created_by=ACTOR.user_id)
    )

    repo = CandidatesExpandedRepository(session)
    items, total = await repo.list_directory(PageParams(page=1, size=50))

    items_map = {item.id: item for item in items}
    assert c1.id in items_map
    assert c2.id in items_map

    item1 = items_map[c1.id]
    assert item1.is_in_talent_pool is True
    assert item1.talent_pool_id == tp_entry.id
    assert item1.candidate_id == c1.id

    item2 = items_map[c2.id]
    assert item2.is_in_talent_pool is False
    assert item2.talent_pool_id is None
    assert item2.candidate_id == c2.id


async def test_list_directory_avoid_duplicate_rows_with_multiple_vacancies(
    session: AsyncSession,
) -> None:
    """A candidate applying to 2 different vacancies must only produce ONE row in directory."""
    cand = await _make_candidate(session, first_name="Multi", last_name="Applicant")
    v1 = await _make_vacancy(session, "Frontend Dev")
    v2 = await _make_vacancy(session, "Fullstack Dev")

    await _apply_to_vacancy(session, cand.id, v1.id, match_score=Decimal("75.00"))
    await _apply_to_vacancy(session, cand.id, v2.id, match_score=Decimal("92.00"))

    # Also add candidate to talent pool from 2 different vacancies (duplicate talent_pool entries)
    tp_repo = BaseRepository(session, TalentPool)
    await tp_repo.add(
        TalentPool(candidate_id=cand.id, source_vacancy_id=v1.id, is_active=True, created_by=1)
    )
    await tp_repo.add(
        TalentPool(candidate_id=cand.id, source_vacancy_id=v2.id, is_active=True, created_by=1)
    )

    repo = CandidatesExpandedRepository(session)
    items, _ = await repo.list_directory(PageParams(page=1, size=100), candidate_id=cand.id)

    # Exactly 1 row for this candidate
    assert len(items) == 1
    row = items[0]
    assert row.id == cand.id
    assert row.is_in_talent_pool is True
    assert len(row.applied_vacancies) == 2
    vac_titles = [v.title for v in row.applied_vacancies]
    assert "Frontend Dev" in vac_titles
    assert "Fullstack Dev" in vac_titles
    assert "Frontend Dev" in row.source_vacancy_title
    assert "Fullstack Dev" in row.source_vacancy_title


async def test_list_directory_filter_by_skill_and_tool(session: AsyncSession) -> None:
    c_py = await _make_candidate(
        session,
        first_name="Pythonista",
        skills=["PythonFastApi", "DjangoExpert"],
        tools=["DockerTool", "GitTool"],
    )
    c_qa = await _make_candidate(
        session,
        first_name="Tester",
        skills=["SeleniumTest", "CypressE2E"],
        tools=["JiraTool", "PostmanTool"],
    )

    repo = CandidatesExpandedRepository(session)

    # Filter skill PythonFastApi
    items, total = await repo.list_directory(PageParams(page=1, size=20), skill="PythonFastApi")
    c_ids = [i.id for i in items]
    assert c_py.id in c_ids
    assert c_qa.id not in c_ids

    # Filter tool JiraTool
    items, total = await repo.list_directory(PageParams(page=1, size=20), tool="JiraTool")
    c_ids = [i.id for i in items]
    assert c_qa.id in c_ids
    assert c_py.id not in c_ids


async def test_list_directory_filter_in_talent_pool(session: AsyncSession) -> None:
    c_pool = await _make_candidate(session, first_name="PoolMember")
    c_nopool = await _make_candidate(session, first_name="NoPoolMember")

    await BaseRepository(session, TalentPool).add(
        TalentPool(candidate_id=c_pool.id, is_active=True, created_by=ACTOR.user_id)
    )

    repo = CandidatesExpandedRepository(session)

    # Only in talent pool
    items, _ = await repo.list_directory(
        PageParams(page=1, size=20),
        search="PoolMember",
        in_talent_pool=True,
    )
    assert any(i.id == c_pool.id for i in items)
    assert not any(i.id == c_nopool.id for i in items)

    # Only not in talent pool
    items, _ = await repo.list_directory(
        PageParams(page=1, size=20),
        search="PoolMember",
        in_talent_pool=False,
    )
    assert not any(i.id == c_pool.id for i in items)
    assert any(i.id == c_nopool.id for i in items)


async def test_list_directory_filter_only_applied(session: AsyncSession) -> None:
    c_applied = await _make_candidate(session, first_name="AppliedCandidate")
    c_unapplied = await _make_candidate(session, first_name="UnappliedCandidate")
    vac = await _make_vacancy(session, "Open Vacancy")
    await _apply_to_vacancy(session, c_applied.id, vac.id)

    repo = CandidatesExpandedRepository(session)

    items, _ = await repo.list_directory(
        PageParams(page=1, size=20),
        search="AppliedCandidate",
        only_applied=True,
    )
    ids = [i.id for i in items]
    assert c_applied.id in ids
    assert c_unapplied.id not in ids


async def test_http_directory_endpoints(client: AsyncClient, session: AsyncSession) -> None:
    """Verifies that both /recruitment/candidates/directory and
    /talent/talent-pool/candidates answer HTTP 200 to staff and return enriched candidates."""
    admin = await bootstrap_admin(session, f"{uuid.uuid4().hex[:12]}@admin.local", "P@ss123456")
    cand = await _make_candidate(
        session, first_name="HttpTester", skills=["FastAPI"], tools=["Docker"]
    )

    # 1. Endpoint in recruitment: /api/v1/recruitment/candidates/directory
    res_rec = await client.get(
        "/api/v1/recruitment/candidates/directory?search=HttpTester",
        headers=_bearer(admin.user_id, portal="staff"),
    )
    assert res_rec.status_code == 200, res_rec.text
    body_rec = res_rec.json()
    assert body_rec["total"] >= 1
    found_rec = next(item for item in body_rec["items"] if item["id"] == cand.id)
    assert found_rec["first_name"] == "HttpTester"
    assert "FastAPI" in found_rec["skills"]
    assert "Docker" in found_rec["tools"]
    assert found_rec["is_in_talent_pool"] is False

    # 2. Endpoint in talent: /api/v1/talent/talent-pool/candidates
    res_tal = await client.get(
        "/api/v1/talent/talent-pool/candidates?search=HttpTester",
        headers=_bearer(admin.user_id, portal="staff"),
    )
    assert res_tal.status_code == 200, res_tal.text
    body_tal = res_tal.json()
    assert body_tal["total"] >= 1
    found_tal = next(item for item in body_tal["items"] if item["id"] == cand.id)
    assert found_tal["first_name"] == "HttpTester"
    assert "FastAPI" in found_tal["skills"]

    # 3. Candidate portal user is forbidden from browsing candidate directory
    portal_cand = await ParameterRepository(session).get_by_type_and_code(
        "user_portal", "candidate"
    )
    assert portal_cand is not None
    user_cand = await UserRepository(session).add(
        User(email=f"{uuid.uuid4().hex[:12]}@cand.local", portal_id=portal_cand.id)
    )
    role = (
        await session.execute(
            select(Role).where(Role.name == CANDIDATE_ROLE_NAME, Role.is_active.is_(True))
        )
    ).scalar_one()
    await assign_role_to_user(session, user_cand.id, role.id)

    res_forbidden = await client.get(
        "/api/v1/recruitment/candidates/directory",
        headers=_bearer(user_cand.id, portal="candidate"),
    )
    assert res_forbidden.status_code == 403