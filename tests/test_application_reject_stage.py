"""Rejecting an application records the stage the candidate had reached.

When a recruiter rejects (sets current_stage_id to None), the prior stage is
captured in rejected_at_stage_id so the candidate UI can show how far they got
before being rejected, instead of an all-empty stepper.
"""

import uuid

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
)
from app.modules.org.infrastructure.parameters_repository import ParameterRepository
from app.modules.recruitment.api.applications_schemas import ApplicationUpdate
from app.modules.recruitment.application.applications_service import ApplicationService
from app.modules.recruitment.infrastructure.application_models import Application
from app.modules.recruitment.infrastructure.applications_repository import (
    ApplicationRepository,
)
from app.modules.recruitment.infrastructure.candidate_models import Candidate
from app.modules.recruitment.infrastructure.models import Vacancy
from app.shared.repository import BaseRepository


def _service(session: AsyncSession) -> ApplicationService:
    return ApplicationService(
        ApplicationRepository(session),
        BaseRepository(session, Vacancy),
        BaseRepository(session, Candidate),
        BaseRepository(session, ProcessStage),
        ParameterRepository(session),
    )


async def _graph(session: AsyncSession) -> tuple[Parameter, Vacancy, ProcessStage, Candidate]:
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


async def test_reject_records_stage_reached(session: AsyncSession) -> None:
    param, vacancy, stage, candidate = await _graph(session)
    application = await BaseRepository(session, Application).add(
        Application(
            vacancy_id=vacancy.id,
            candidate_id=candidate.id,
            status_id=param.id,
            current_stage_id=stage.id,
        )
    )
    await session.flush()

    actor = CurrentUser(user_id=1, ip=None)
    updated = await _service(session).update(
        application.id, ApplicationUpdate(current_stage_id=None), actor
    )

    rejected = await ParameterRepository(session).get_by_type_and_code(
        "application_status", "rejected"
    )
    assert rejected is not None, "application_status:rejected must be seeded"
    assert updated.current_stage_id is None
    assert updated.status_id == rejected.id
    assert updated.rejected_at_stage_id == stage.id


async def test_reject_without_prior_stage_records_none(session: AsyncSession) -> None:
    # Defensive: rejecting an application that had no stage leaves the field None.
    param, vacancy, _stage, candidate = await _graph(session)
    application = await BaseRepository(session, Application).add(
        Application(
            vacancy_id=vacancy.id,
            candidate_id=candidate.id,
            status_id=param.id,
            current_stage_id=None,
        )
    )
    await session.flush()

    actor = CurrentUser(user_id=1, ip=None)
    updated = await _service(session).update(
        application.id, ApplicationUpdate(current_stage_id=None), actor
    )
    assert updated.rejected_at_stage_id is None
