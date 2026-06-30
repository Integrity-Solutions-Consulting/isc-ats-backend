import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import CurrentUser
from app.modules.auth.infrastructure.models import User
from app.modules.org.infrastructure.models import (
    ClientCompany,
    Contact,
    Department,
    Parameter,
    Process,
)
from app.modules.org.infrastructure.parameters_repository import ParameterRepository
from app.modules.recruitment.infrastructure.candidate_models import Candidate
from app.modules.recruitment.infrastructure.models import Vacancy
from app.modules.talent.api.talent_pool_schemas import TalentPoolCreate
from app.modules.talent.application.talent_pool_service import (
    DuplicateTalentPoolError,
    TalentPoolNotFoundError,
    TalentPoolReferenceError,
    TalentPoolService,
)
from app.modules.talent.infrastructure.models import TalentPool
from app.shared.repository import BaseRepository

ACTOR = CurrentUser(user_id=1, ip="127.0.0.1")


def _service(session: AsyncSession) -> TalentPoolService:
    return TalentPoolService(
        BaseRepository(session, TalentPool),
        BaseRepository(session, Candidate),
        BaseRepository(session, Vacancy),
    )


async def _make_candidate(session: AsyncSession) -> Candidate:
    portal = await ParameterRepository(session).get_by_type_and_code("user_portal", "staff")
    assert portal is not None
    user = await BaseRepository(session, User).add(
        User(email=f"{uuid.uuid4().hex[:12]}@test.local", portal_id=portal.id)
    )
    return await BaseRepository(session, Candidate).add(
        Candidate(user_id=user.id, first_name="Ana", last_name="Diaz")
    )


async def _make_vacancy(session: AsyncSession) -> Vacancy:
    param = await BaseRepository(session, Parameter).add(
        Parameter(type="x", code=uuid.uuid4().hex[:8], name="P")
    )
    company = await BaseRepository(session, ClientCompany).add(ClientCompany(name="ACME"))
    contact = await BaseRepository(session, Contact).add(
        Contact(
            client_company_id=company.id,
            first_name="R",
            last_name="S",
            email=f"{uuid.uuid4().hex[:6]}@acme.com",
        )
    )
    dept = await BaseRepository(session, Department).add(Department(name="T"))
    process = await BaseRepository(session, Process).add(
        Process(
            client_company_id=company.id,
            department_id=dept.id,
            name=f"Proc {uuid.uuid4().hex[:6]}",
        )
    )
    return await BaseRepository(session, Vacancy).add(
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
            created_by=1,
        )
    )


async def test_add_candidate_no_vacancy(session: AsyncSession) -> None:
    candidate = await _make_candidate(session)
    entry = await _service(session).create(
        TalentPoolCreate(candidate_id=candidate.id), ACTOR
    )

    assert entry.id is not None
    assert entry.candidate_id == candidate.id
    assert entry.source_vacancy_id is None
    assert entry.is_active is True
    assert entry.created_by == ACTOR.user_id


async def test_add_candidate_with_source_vacancy(session: AsyncSession) -> None:
    candidate = await _make_candidate(session)
    vacancy = await _make_vacancy(session)
    entry = await _service(session).create(
        TalentPoolCreate(candidate_id=candidate.id, source_vacancy_id=vacancy.id), ACTOR
    )

    assert entry.source_vacancy_id == vacancy.id


async def test_create_rejects_unknown_candidate(session: AsyncSession) -> None:
    with pytest.raises(TalentPoolReferenceError):
        await _service(session).create(TalentPoolCreate(candidate_id=999999), ACTOR)


async def test_create_rejects_unknown_vacancy(session: AsyncSession) -> None:
    candidate = await _make_candidate(session)
    with pytest.raises(TalentPoolReferenceError):
        await _service(session).create(
            TalentPoolCreate(candidate_id=candidate.id, source_vacancy_id=999999), ACTOR
        )


async def test_get_not_found(session: AsyncSession) -> None:
    with pytest.raises(TalentPoolNotFoundError):
        await _service(session).get(999999)


async def test_delete_soft_deletes(session: AsyncSession) -> None:
    candidate = await _make_candidate(session)
    svc = _service(session)
    entry = await svc.create(TalentPoolCreate(candidate_id=candidate.id), ACTOR)

    await svc.delete(entry.id)

    with pytest.raises(TalentPoolNotFoundError):
        await svc.get(entry.id)


async def test_create_rejects_duplicate_same_vacancy(session: AsyncSession) -> None:
    candidate = await _make_candidate(session)
    vacancy = await _make_vacancy(session)
    svc = _service(session)
    await svc.create(
        TalentPoolCreate(candidate_id=candidate.id, source_vacancy_id=vacancy.id), ACTOR
    )
    with pytest.raises(DuplicateTalentPoolError):
        await svc.create(
            TalentPoolCreate(candidate_id=candidate.id, source_vacancy_id=vacancy.id), ACTOR
        )


async def test_create_allows_same_candidate_different_vacancies(
    session: AsyncSession,
) -> None:
    candidate = await _make_candidate(session)
    v1 = await _make_vacancy(session)
    v2 = await _make_vacancy(session)
    svc = _service(session)
    e1 = await svc.create(
        TalentPoolCreate(candidate_id=candidate.id, source_vacancy_id=v1.id), ACTOR
    )
    e2 = await svc.create(
        TalentPoolCreate(candidate_id=candidate.id, source_vacancy_id=v2.id), ACTOR
    )
    assert e1.id != e2.id


async def test_create_rejects_duplicate_general_entry(session: AsyncSession) -> None:
    candidate = await _make_candidate(session)
    svc = _service(session)
    await svc.create(TalentPoolCreate(candidate_id=candidate.id), ACTOR)
    with pytest.raises(DuplicateTalentPoolError):
        await svc.create(TalentPoolCreate(candidate_id=candidate.id), ACTOR)


async def test_create_allows_readd_after_remove(session: AsyncSession) -> None:
    candidate = await _make_candidate(session)
    vacancy = await _make_vacancy(session)
    svc = _service(session)
    e1 = await svc.create(
        TalentPoolCreate(candidate_id=candidate.id, source_vacancy_id=vacancy.id), ACTOR
    )
    await svc.delete(e1.id)
    e2 = await svc.create(
        TalentPoolCreate(candidate_id=candidate.id, source_vacancy_id=vacancy.id), ACTOR
    )
    assert e2.id != e1.id


async def test_list_filtered_by_candidate(session: AsyncSession) -> None:
    c1 = await _make_candidate(session)
    c2 = await _make_candidate(session)
    svc = _service(session)
    await svc.create(TalentPoolCreate(candidate_id=c1.id), ACTOR)
    await svc.create(TalentPoolCreate(candidate_id=c2.id), ACTOR)

    from app.shared.pagination import PageParams

    items, total = await svc.list(PageParams(page=1, size=20), candidate_id=c1.id)
    assert total == 1
    assert items[0].candidate_id == c1.id
