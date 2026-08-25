"""The Kanban pipeline payload carries the fields the board filters on.

Talento Humano filters candidates by city, by whether they are currently
studying, and by salary expectation. Those three must travel on every card, and
an undeclared salary expectation must stay distinguishable from a declared 0 —
collapsing both into 0 would make undeclared applicants match any range starting
at 0 and silently corrupt the filter.
"""

import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

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
from app.modules.recruitment.infrastructure.application_models import Application
from app.modules.recruitment.infrastructure.candidate_models import Candidate
from app.modules.recruitment.infrastructure.models import Vacancy
from app.modules.recruitment.infrastructure.pipeline_repository import PipelineRepository
from app.shared.repository import BaseRepository


async def _candidate(
    session: AsyncSession,
    *,
    city_id: int | None,
    is_studying: bool,
) -> Candidate:
    portal = await ParameterRepository(session).get_by_type_and_code("user_portal", "staff")
    assert portal is not None
    user = await BaseRepository(session, User).add(
        User(email=f"{uuid.uuid4().hex[:12]}@test.local", portal_id=portal.id)
    )
    return await BaseRepository(session, Candidate).add(
        Candidate(
            user_id=user.id,
            first_name="J",
            last_name="P",
            city_id=city_id,
            is_studying=is_studying,
        )
    )


async def _graph(session: AsyncSession) -> tuple[Parameter, Parameter, Vacancy, ProcessStage]:
    param = await BaseRepository(session, Parameter).add(
        Parameter(type="x", code=uuid.uuid4().hex[:8], name="P")
    )
    city = await BaseRepository(session, Parameter).add(
        Parameter(type="city", code=uuid.uuid4().hex[:8], name="Guayaquil")
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
    stage = await BaseRepository(session, ProcessStage).add(
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
    return param, city, vacancy, stage


async def test_pipeline_cards_carry_city_and_is_studying(session: AsyncSession) -> None:
    param, city, vacancy, stage = await _graph(session)
    candidate = await _candidate(session, city_id=city.id, is_studying=True)
    await BaseRepository(session, Application).add(
        Application(
            vacancy_id=vacancy.id,
            candidate_id=candidate.id,
            status_id=param.id,
            current_stage_id=stage.id,
        )
    )
    await session.flush()

    data = await PipelineRepository(session).get_pipeline(vacancy.id)

    assert len(data.cards) == 1
    assert data.cards[0].city == "Guayaquil"
    assert data.cards[0].is_studying is True


async def test_candidate_without_city_still_appears_on_the_board(
    session: AsyncSession,
) -> None:
    """candidates.city_id is nullable — an inner join would drop these applicants
    from the Kanban entirely, which is far worse than an unfilterable card."""
    param, _city, vacancy, stage = await _graph(session)
    candidate = await _candidate(session, city_id=None, is_studying=False)
    await BaseRepository(session, Application).add(
        Application(
            vacancy_id=vacancy.id,
            candidate_id=candidate.id,
            status_id=param.id,
            current_stage_id=stage.id,
        )
    )
    await session.flush()

    data = await PipelineRepository(session).get_pipeline(vacancy.id)

    assert len(data.cards) == 1
    assert data.cards[0].city is None


async def test_undeclared_salary_is_none_and_declared_zero_is_zero(
    session: AsyncSession,
) -> None:
    param, city, vacancy, stage = await _graph(session)
    undeclared = await _candidate(session, city_id=city.id, is_studying=False)
    declared_zero = await _candidate(session, city_id=city.id, is_studying=False)
    for candidate, salary in ((undeclared, None), (declared_zero, Decimal("0"))):
        await BaseRepository(session, Application).add(
            Application(
                vacancy_id=vacancy.id,
                candidate_id=candidate.id,
                status_id=param.id,
                current_stage_id=stage.id,
                salary_expectation=salary,
            )
        )
    await session.flush()

    data = await PipelineRepository(session).get_pipeline(vacancy.id)
    by_candidate = {c.candidate_id: c.salary_expectation for c in data.cards}

    assert by_candidate[undeclared.id] is None
    assert by_candidate[declared_zero.id] == Decimal("0")


async def test_pipeline_endpoint_serializes_undeclared_salary_as_null(
    session: AsyncSession,
) -> None:
    """End-to-end: the route must not fold an undeclared expectation into 0.

    The repository can return None and the JSON still say 0 if the mapping uses a
    truthiness check — which is exactly what it used to do.
    """
    from httpx import ASGITransport, AsyncClient

    from app.core.database import get_session
    from app.core.security import create_access_token
    from app.main import app
    from app.modules.auth.application.bootstrap_service import bootstrap_admin

    param, city, vacancy, stage = await _graph(session)
    undeclared = await _candidate(session, city_id=city.id, is_studying=True)
    declared_zero = await _candidate(session, city_id=None, is_studying=False)
    for candidate, salary in ((undeclared, None), (declared_zero, Decimal("0"))):
        await BaseRepository(session, Application).add(
            Application(
                vacancy_id=vacancy.id,
                candidate_id=candidate.id,
                status_id=param.id,
                current_stage_id=stage.id,
                salary_expectation=salary,
            )
        )
    await session.flush()

    admin = await bootstrap_admin(session, f"{uuid.uuid4().hex[:12]}@test.local", "S3cret")
    token = create_access_token(admin.user_id, extra_claims={"portal": "staff"})

    async def _use_test_session():
        yield session

    app.dependency_overrides[get_session] = _use_test_session
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/api/v1/recruitment/vacancies/{vacancy.id}/pipeline",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 200, response.text
    finally:
        app.dependency_overrides.clear()

    cards = {c["candidateId"]: c for c in response.json()["cards"]}

    assert cards[str(undeclared.id)]["salaryExpectation"] is None
    assert cards[str(undeclared.id)]["city"] == "Guayaquil"
    assert cards[str(undeclared.id)]["isStudying"] is True

    assert cards[str(declared_zero.id)]["salaryExpectation"] == 0
    assert cards[str(declared_zero.id)]["city"] is None
    assert cards[str(declared_zero.id)]["isStudying"] is False


# ── years_of_experience — Candidate profile field surfaced on the card ─────────
#
# Unlike salary_expectation (an application-time field), years_of_experience
# lives on the candidate row. Same undeclared-vs-zero hazard applies: the board
# will filter by "minimum years of experience," and folding None into 0 would
# make an undeclared candidate satisfy any minimum starting at 0.


async def test_pipeline_card_surfaces_decimal_years_of_experience(
    session: AsyncSession,
) -> None:
    """A decimal value like 1.5 must not be truncated to an int on the way out."""
    param, city, vacancy, stage = await _graph(session)
    candidate = await _candidate(session, city_id=city.id, is_studying=False)
    candidate.years_of_experience = Decimal("1.5")
    await BaseRepository(session, Application).add(
        Application(
            vacancy_id=vacancy.id,
            candidate_id=candidate.id,
            status_id=param.id,
            current_stage_id=stage.id,
        )
    )
    await session.flush()

    data = await PipelineRepository(session).get_pipeline(vacancy.id)

    assert len(data.cards) == 1
    assert data.cards[0].years_of_experience == Decimal("1.5")


async def test_undeclared_years_of_experience_is_none_and_declared_zero_is_zero(
    session: AsyncSession,
) -> None:
    param, city, vacancy, stage = await _graph(session)
    undeclared = await _candidate(session, city_id=city.id, is_studying=False)
    declared_zero = await _candidate(session, city_id=city.id, is_studying=False)
    declared_zero.years_of_experience = Decimal("0")
    for candidate in (undeclared, declared_zero):
        await BaseRepository(session, Application).add(
            Application(
                vacancy_id=vacancy.id,
                candidate_id=candidate.id,
                status_id=param.id,
                current_stage_id=stage.id,
            )
        )
    await session.flush()

    data = await PipelineRepository(session).get_pipeline(vacancy.id)
    by_candidate = {c.candidate_id: c.years_of_experience for c in data.cards}

    assert by_candidate[undeclared.id] is None
    assert by_candidate[declared_zero.id] == Decimal("0")


async def test_pipeline_endpoint_serializes_undeclared_years_of_experience_as_null(
    session: AsyncSession,
) -> None:
    """End-to-end: the route must not fold an undeclared years_of_experience into 0.

    The repository can return None and the JSON still say 0 if the mapping uses a
    truthiness check — which is exactly what bit salaryExpectation before.
    """
    from httpx import ASGITransport, AsyncClient

    from app.core.database import get_session
    from app.core.security import create_access_token
    from app.main import app
    from app.modules.auth.application.bootstrap_service import bootstrap_admin

    param, city, vacancy, stage = await _graph(session)
    undeclared = await _candidate(session, city_id=city.id, is_studying=True)
    declared_zero = await _candidate(session, city_id=None, is_studying=False)
    declared_zero.years_of_experience = Decimal("0")
    for candidate in (undeclared, declared_zero):
        await BaseRepository(session, Application).add(
            Application(
                vacancy_id=vacancy.id,
                candidate_id=candidate.id,
                status_id=param.id,
                current_stage_id=stage.id,
            )
        )
    await session.flush()

    admin = await bootstrap_admin(session, f"{uuid.uuid4().hex[:12]}@test.local", "S3cret")
    token = create_access_token(admin.user_id, extra_claims={"portal": "staff"})

    async def _use_test_session():
        yield session

    app.dependency_overrides[get_session] = _use_test_session
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/api/v1/recruitment/vacancies/{vacancy.id}/pipeline",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 200, response.text
    finally:
        app.dependency_overrides.clear()

    cards = {c["candidateId"]: c for c in response.json()["cards"]}

    assert cards[str(undeclared.id)]["yearsOfExperience"] is None
    assert cards[str(declared_zero.id)]["yearsOfExperience"] == 0
