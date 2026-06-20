"""Business rule: a vacancy can only be moved to 'closed' once every opening is
filled (hired_count >= openings). 'cancelled' closes it without that requirement;
other transitions (paused/active/draft) are unrestricted.
"""

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
    ProcessStage,
    ProfileTemplate,
)
from app.modules.org.infrastructure.parameters_repository import ParameterRepository
from app.modules.recruitment.api.applications_schemas import (
    ApplicationCreate,
    ApplicationUpdate,
)
from app.modules.recruitment.api.vacancies_schemas import VacancyUpdate
from app.modules.recruitment.application.applications_service import ApplicationService
from app.modules.recruitment.application.vacancies_service import (
    VacancyCloseError,
    VacancyService,
)
from app.modules.recruitment.infrastructure.applications_repository import (
    ApplicationRepository,
)
from app.modules.recruitment.infrastructure.candidate_models import Candidate
from app.modules.recruitment.infrastructure.models import Vacancy
from app.modules.recruitment.infrastructure.pipeline_repository import PipelineRepository
from app.shared.repository import BaseRepository

ACTOR = CurrentUser(user_id=1, ip="127.0.0.1")


def _service(session: AsyncSession) -> VacancyService:
    return VacancyService(
        BaseRepository(session, Vacancy),
        BaseRepository(session, Parameter),
        BaseRepository(session, ClientCompany),
        BaseRepository(session, Contact),
        BaseRepository(session, Department),
        BaseRepository(session, Process),
        BaseRepository(session, ProfileTemplate),
        PipelineRepository(session),
    )


async def _status(session: AsyncSession, code: str) -> Parameter:
    p = await ParameterRepository(session).get_by_type_and_code("vacancy_status", code)
    assert p is not None, f"vacancy_status:{code} must be seeded"
    return p


async def _setup_vacancy(session: AsyncSession, *, openings: int, hired: int) -> Vacancy:
    sp = await BaseRepository(session, Parameter).add(
        Parameter(type="stage", code=uuid.uuid4().hex[:8], name="Contratado")
    )
    company = await BaseRepository(session, ClientCompany).add(ClientCompany(name="CloseCo"))
    contact = await BaseRepository(session, Contact).add(
        Contact(client_company_id=company.id, first_name="C", last_name="D", email="c@d.co")
    )
    dept = await BaseRepository(session, Department).add(Department(name="Ops"))
    process = await BaseRepository(session, Process).add(
        Process(
            client_company_id=company.id,
            department_id=dept.id,
            name=f"P{uuid.uuid4().hex[:6]}",
        )
    )
    final_stage = await BaseRepository(session, ProcessStage).add(
        ProcessStage(process_id=process.id, stage_id=sp.id, order=1, is_final_positive=True)
    )
    active = await _status(session, "active")
    vacancy = await BaseRepository(session, Vacancy).add(
        Vacancy(
            vacancy_name_id=sp.id,
            client_company_id=company.id,
            contact_id=contact.id,
            department_id=dept.id,
            process_id=process.id,
            career_id=sp.id,
            city_id=sp.id,
            work_mode_id=sp.id,
            resource_level_id=sp.id,
            status_id=active.id,
            openings=openings,
        )
    )

    portal = await ParameterRepository(session).get_by_type_and_code("user_portal", "staff")
    assert portal is not None
    app_service = ApplicationService(
        ApplicationRepository(session),
        BaseRepository(session, Vacancy),
        BaseRepository(session, Candidate),
        BaseRepository(session, ProcessStage),
        BaseRepository(session, Parameter),
    )
    for i in range(hired):
        user = await BaseRepository(session, User).add(
            User(email=f"{uuid.uuid4().hex[:12]}@test.local", portal_id=portal.id)
        )
        cand = await BaseRepository(session, Candidate).add(
            Candidate(user_id=user.id, first_name=f"H{i}", last_name="Hired")
        )
        app = await app_service.create(
            ApplicationCreate(vacancy_id=vacancy.id, candidate_id=cand.id, status_id=sp.id),
            ACTOR,
        )
        await app_service.update(
            app.id, ApplicationUpdate(current_stage_id=final_stage.id), ACTOR
        )
    return vacancy


async def test_close_blocked_when_openings_not_filled(session: AsyncSession) -> None:
    vacancy = await _setup_vacancy(session, openings=2, hired=1)
    closed = await _status(session, "closed")
    with pytest.raises(VacancyCloseError):
        await _service(session).update(vacancy.id, VacancyUpdate(status_id=closed.id), ACTOR)


async def test_close_allowed_when_openings_filled(session: AsyncSession) -> None:
    vacancy = await _setup_vacancy(session, openings=2, hired=2)
    closed = await _status(session, "closed")
    updated = await _service(session).update(
        vacancy.id, VacancyUpdate(status_id=closed.id), ACTOR
    )
    assert updated.status_id == closed.id


async def test_cancel_allowed_when_not_filled(session: AsyncSession) -> None:
    vacancy = await _setup_vacancy(session, openings=2, hired=0)
    cancelled = await _status(session, "cancelled")
    updated = await _service(session).update(
        vacancy.id, VacancyUpdate(status_id=cancelled.id), ACTOR
    )
    assert updated.status_id == cancelled.id


async def test_pause_allowed_when_not_filled(session: AsyncSession) -> None:
    vacancy = await _setup_vacancy(session, openings=2, hired=0)
    paused = await _status(session, "paused")
    updated = await _service(session).update(
        vacancy.id, VacancyUpdate(status_id=paused.id), ACTOR
    )
    assert updated.status_id == paused.id


async def test_non_status_update_still_works(session: AsyncSession) -> None:
    vacancy = await _setup_vacancy(session, openings=2, hired=0)
    updated = await _service(session).update(
        vacancy.id, VacancyUpdate(openings=5), ACTOR
    )
    assert updated.openings == 5
