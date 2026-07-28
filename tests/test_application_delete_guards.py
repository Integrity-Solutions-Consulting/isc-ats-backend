"""Delete guards driven by active applications.

A vacancy can only be blocked from deletion by an actual HIRE on record (cancel
it instead to preserve that outcome) — every other active application is
auto-rejected as part of the delete instead of blocking it. A process stage
that still holds an application cannot be deleted.
"""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.infrastructure.models import User
from app.modules.org.application.process_stages_service import (
    ProcessStageInUseError,
    ProcessStageService,
)
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
from app.modules.org.infrastructure.process_stages_repository import (
    ProcessStageRepository,
)
from app.modules.recruitment.application.applications_service import ApplicationService
from app.modules.recruitment.application.vacancies_service import (
    AUTO_REJECT_REASON,
    VacancyInUseError,
    VacancyService,
)
from app.modules.recruitment.infrastructure.application_models import Application
from app.modules.recruitment.infrastructure.application_usage_repository import (
    ApplicationUsageRepository,
)
from app.modules.recruitment.infrastructure.applications_repository import (
    ApplicationRepository,
)
from app.modules.recruitment.infrastructure.candidate_models import Candidate
from app.modules.recruitment.infrastructure.models import Vacancy
from app.modules.recruitment.infrastructure.pipeline_repository import (
    PipelineRepository,
)
from app.shared.repository import BaseRepository


def _application_service(session: AsyncSession) -> ApplicationService:
    return ApplicationService(
        ApplicationRepository(session),
        BaseRepository(session, Vacancy),
        BaseRepository(session, Candidate),
        BaseRepository(session, ProcessStage),
        ParameterRepository(session),
    )


def _vacancy_service(session: AsyncSession) -> VacancyService:
    usage = ApplicationUsageRepository(session)
    return VacancyService(
        BaseRepository(session, Vacancy),
        BaseRepository(session, Parameter),
        BaseRepository(session, ClientCompany),
        BaseRepository(session, Contact),
        BaseRepository(session, Department),
        BaseRepository(session, Process),
        BaseRepository(session, ProfileTemplate),
        PipelineRepository(session),
        applications_checker=usage.has_active_for_vacancy,
        application_rejector=_application_service(session).auto_reject_for_vacancy,
    )


def _stage_service(session: AsyncSession) -> ProcessStageService:
    usage = ApplicationUsageRepository(session)
    return ProcessStageService(
        ProcessStageRepository(session),
        BaseRepository(session, Process),
        BaseRepository(session, Parameter),
        in_use_checker=usage.has_active_in_stage,
    )


async def _graph(session: AsyncSession) -> tuple[Parameter, Vacancy, ProcessStage, Candidate]:
    param = await BaseRepository(session, Parameter).add(
        Parameter(type="x", code=uuid.uuid4().hex[:8], name="P")
    )
    company = await BaseRepository(session, ClientCompany).add(ClientCompany(name="Co"))
    contact = await BaseRepository(session, Contact).add(
        Contact(client_company_id=company.id, first_name="C", last_name="D", email="c@d.co")
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
    portal = await ParameterRepository(session).get_by_type_and_code("user_portal", "staff")
    assert portal is not None
    user = await BaseRepository(session, User).add(
        User(email=f"{uuid.uuid4().hex[:12]}@test.local", portal_id=portal.id)
    )
    candidate = await BaseRepository(session, Candidate).add(
        Candidate(user_id=user.id, first_name="J", last_name="P")
    )
    return param, vacancy, stage, candidate


async def _apply(session, param, vacancy, candidate, stage) -> Application:
    return await BaseRepository(session, Application).add(
        Application(
            vacancy_id=vacancy.id,
            candidate_id=candidate.id,
            status_id=param.id,
            current_stage_id=stage.id,
        )
    )


# ── Vacancy ──────────────────────────────────────────────────────────────────


async def test_vacancy_delete_blocked_by_hired_application(session: AsyncSession) -> None:
    """Only an actual hire blocks deletion — cancel the vacancy to preserve it."""
    param, vacancy, stage, candidate = await _graph(session)
    hired = await ParameterRepository(session).get_by_type_and_code(
        "application_status", "hired"
    )
    assert hired is not None, "application_status:hired must be seeded"
    await BaseRepository(session, Application).add(
        Application(
            vacancy_id=vacancy.id,
            candidate_id=candidate.id,
            status_id=hired.id,
            current_stage_id=stage.id,
        )
    )
    with pytest.raises(VacancyInUseError):
        await _vacancy_service(session).delete(vacancy.id)


async def test_vacancy_delete_auto_rejects_non_hired_active_application(
    session: AsyncSession,
) -> None:
    """A non-hired active application no longer blocks deletion — it is
    auto-rejected (with the fixed system reason) as part of the same delete."""
    param, vacancy, stage, candidate = await _graph(session)
    application = await _apply(session, param, vacancy, candidate, stage)

    await _vacancy_service(session).delete(vacancy.id)

    refreshed_vacancy = await session.get(Vacancy, vacancy.id)
    assert refreshed_vacancy is not None and refreshed_vacancy.is_active is False

    rejected = await ParameterRepository(session).get_by_type_and_code(
        "application_status", "rejected"
    )
    assert rejected is not None, "application_status:rejected must be seeded"
    refreshed_app = await session.get(Application, application.id)
    assert refreshed_app is not None
    assert refreshed_app.status_id == rejected.id
    assert refreshed_app.current_stage_id is None
    assert refreshed_app.rejected_at_stage_id == stage.id
    assert refreshed_app.rejection_reason == AUTO_REJECT_REASON


async def test_vacancy_delete_allowed_when_no_applications(session: AsyncSession) -> None:
    _param, vacancy, _stage, _candidate = await _graph(session)
    await _vacancy_service(session).delete(vacancy.id)
    refreshed = await session.get(Vacancy, vacancy.id)
    assert refreshed is not None and refreshed.is_active is False


async def test_vacancy_delete_allowed_when_application_withdrawn(session: AsyncSession) -> None:
    param, vacancy, stage, candidate = await _graph(session)
    application = await _apply(session, param, vacancy, candidate, stage)
    await BaseRepository(session, Application).soft_delete(application)
    await _vacancy_service(session).delete(vacancy.id)
    refreshed = await session.get(Vacancy, vacancy.id)
    assert refreshed is not None and refreshed.is_active is False


# ── Process stage ────────────────────────────────────────────────────────────


async def test_stage_delete_blocked_by_application_in_stage(session: AsyncSession) -> None:
    param, vacancy, stage, candidate = await _graph(session)
    await _apply(session, param, vacancy, candidate, stage)
    with pytest.raises(ProcessStageInUseError):
        await _stage_service(session).delete(stage.id)


async def test_stage_delete_allowed_when_empty(session: AsyncSession) -> None:
    _param, _vacancy, stage, _candidate = await _graph(session)
    await _stage_service(session).delete(stage.id)
    refreshed = await session.get(ProcessStage, stage.id)
    assert refreshed is not None and refreshed.is_active is False
