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
from app.modules.recruitment.api.applications_schemas import ApplicationCreate, ApplicationUpdate
from app.modules.recruitment.api.vacancies_schemas import VacancyCreate, VacancyUpdate
from app.modules.recruitment.application.applications_service import ApplicationService
from app.modules.recruitment.application.vacancies_service import (
    VacancyReferenceError,
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
_CAN_PUBLISH: set[str] = {"recruitment.vacancies.publish", "recruitment.vacancies.create"}


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


async def _valid_payload(session: AsyncSession, **overrides) -> VacancyCreate:
    """Build a vacancy payload backed by a real org graph (FKs all resolve)."""
    param = await BaseRepository(session, Parameter).add(
        Parameter(type="x", code=uuid.uuid4().hex[:8], name="Param")
    )
    company = await BaseRepository(session, ClientCompany).add(ClientCompany(name="ACME"))
    contact = await BaseRepository(session, Contact).add(
        Contact(
            client_company_id=company.id,
            first_name="Ana",
            last_name="Diaz",
            email="ana@acme.com",
        )
    )
    department = await BaseRepository(session, Department).add(Department(name="Tech"))
    process = await BaseRepository(session, Process).add(
        Process(
            client_company_id=company.id,
            department_id=department.id,
            name=f"Proc {uuid.uuid4().hex[:6]}",
        )
    )
    payload = {
        "vacancy_name_id": param.id,
        "client_company_id": company.id,
        "contact_id": contact.id,
        "department_id": department.id,
        "process_id": process.id,
        "career_id": param.id,
        "city_id": param.id,
        "work_mode_id": param.id,
        "resource_level_id": param.id,
        "status_id": param.id,
    }
    payload.update(overrides)
    return VacancyCreate(**payload)


async def test_create_vacancy_with_valid_refs(session: AsyncSession) -> None:
    data = await _valid_payload(
        session, profile_requirements={"skills": ["python", "sql"]}, openings=3
    )
    vacancy = await _service(session).create(data, ACTOR)

    assert vacancy.id is not None
    assert vacancy.openings == 3
    assert vacancy.profile_requirements == {"skills": ["python", "sql"]}
    assert vacancy.is_active is True
    assert vacancy.created_by == ACTOR.user_id


async def test_create_vacancy_rejects_unknown_company(session: AsyncSession) -> None:
    data = await _valid_payload(session, client_company_id=999999)
    with pytest.raises(VacancyReferenceError):
        await _service(session).create(data, ACTOR)


async def test_create_vacancy_rejects_unknown_parameter(session: AsyncSession) -> None:
    # A publisher providing an invalid status_id must still get a VacancyReferenceError.
    # Non-publishers have status_id overridden before validation (solicitud-forcing), so
    # this test uses a publisher caller to exercise the validation path.
    data = await _valid_payload(session, status_id=999999)
    with pytest.raises(VacancyReferenceError):
        await _service(session).create(data, ACTOR, caller_permission_codes=_CAN_PUBLISH)


async def test_update_vacancy_validates_changed_ref(session: AsyncSession) -> None:
    service = _service(session)
    vacancy = await service.create(await _valid_payload(session), ACTOR)

    with pytest.raises(VacancyReferenceError):
        await service.update(vacancy.id, VacancyUpdate(department_id=999999), ACTOR)


async def test_pipeline_returns_stages_and_cards(session: AsyncSession) -> None:
    # Build vacancy with a process that has one stage
    param = await BaseRepository(session, Parameter).add(
        Parameter(type="x", code=uuid.uuid4().hex[:8], name="StageName")
    )
    company = await BaseRepository(session, ClientCompany).add(ClientCompany(name="PipelineCo"))
    contact = await BaseRepository(session, Contact).add(
        Contact(client_company_id=company.id, first_name="C", last_name="D", email="c@d.co")
    )
    dept = await BaseRepository(session, Department).add(Department(name="Eng"))
    process = await BaseRepository(session, Process).add(
        Process(
            client_company_id=company.id, department_id=dept.id, name=f"P{uuid.uuid4().hex[:6]}"
        )
    )
    await BaseRepository(session, ProcessStage).add(
        ProcessStage(process_id=process.id, stage_id=param.id, order=1, is_final_positive=False)
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

    # Add one application
    portal = await ParameterRepository(session).get_by_type_and_code("user_portal", "staff")
    assert portal is not None
    user = await BaseRepository(session, User).add(
        User(email=f"{uuid.uuid4().hex[:12]}@test.local", portal_id=portal.id)
    )
    candidate = await BaseRepository(session, Candidate).add(
        Candidate(user_id=user.id, first_name="Maria", last_name="Torres")
    )
    app_service = ApplicationService(
        ApplicationRepository(session),
        BaseRepository(session, Vacancy),
        BaseRepository(session, Candidate),
        BaseRepository(session, ProcessStage),
        ParameterRepository(session),
    )
    await app_service.create(
        ApplicationCreate(vacancy_id=vacancy.id, candidate_id=candidate.id, status_id=param.id),
        ACTOR,
    )

    pipeline = await PipelineRepository(session).get_pipeline(vacancy.id)

    assert len(pipeline.stages) == 1
    assert pipeline.stages[0].name == "StageName"
    assert pipeline.stages[0].order == 1
    assert len(pipeline.cards) == 1
    assert pipeline.cards[0].vacancy_id == vacancy.id
    assert pipeline.cards[0].first_name == "Maria"


async def test_pipeline_empty_vacancy(session: AsyncSession) -> None:
    vacancy = await _service(session).create(await _valid_payload(session), ACTOR)
    pipeline = await PipelineRepository(session).get_pipeline(vacancy.id)
    assert pipeline.stages == []
    assert pipeline.cards == []
    assert pipeline.rejected_count == 0


async def test_pipeline_hired_count_increments_when_candidate_reaches_final_positive_stage(
    session: AsyncSession,
) -> None:
    """Fix A: hired_count and openings are returned correctly in PipelineData.

    One candidate placed in the final-positive stage → hired_count=1.
    Vacancy has openings=2 → openings=2.
    """
    stage_param = await BaseRepository(session, Parameter).add(
        Parameter(type="stage", code=uuid.uuid4().hex[:8], name="Contratado")
    )
    company = await BaseRepository(session, ClientCompany).add(ClientCompany(name="HiredCo"))
    contact = await BaseRepository(session, Contact).add(
        Contact(client_company_id=company.id, first_name="H", last_name="R", email="h@r.co")
    )
    dept = await BaseRepository(session, Department).add(Department(name="Ops"))
    process = await BaseRepository(session, Process).add(
        Process(
            client_company_id=company.id, department_id=dept.id, name=f"P{uuid.uuid4().hex[:6]}"
        )
    )
    final_stage = await BaseRepository(session, ProcessStage).add(
        ProcessStage(
            process_id=process.id, stage_id=stage_param.id, order=1, is_final_positive=True
        )
    )
    vacancy = await BaseRepository(session, Vacancy).add(
        Vacancy(
            vacancy_name_id=stage_param.id,
            client_company_id=company.id,
            contact_id=contact.id,
            department_id=dept.id,
            process_id=process.id,
            career_id=stage_param.id,
            city_id=stage_param.id,
            work_mode_id=stage_param.id,
            resource_level_id=stage_param.id,
            status_id=stage_param.id,
            openings=2,
        )
    )

    portal = await ParameterRepository(session).get_by_type_and_code("user_portal", "staff")
    assert portal is not None
    user = await BaseRepository(session, User).add(
        User(email=f"{uuid.uuid4().hex[:12]}@test.local", portal_id=portal.id)
    )
    candidate = await BaseRepository(session, Candidate).add(
        Candidate(user_id=user.id, first_name="Carlos", last_name="Hired")
    )

    app_service = ApplicationService(
        ApplicationRepository(session),
        BaseRepository(session, Vacancy),
        BaseRepository(session, Candidate),
        BaseRepository(session, ProcessStage),
        ParameterRepository(session),
    )
    app = await app_service.create(
        ApplicationCreate(
            vacancy_id=vacancy.id, candidate_id=candidate.id, status_id=stage_param.id
        ),
        ACTOR,
    )

    # Move application to the final-positive stage
    await app_service.update(app.id, ApplicationUpdate(current_stage_id=final_stage.id), ACTOR)

    pipeline = await PipelineRepository(session).get_pipeline(vacancy.id)

    assert pipeline.hired_count == 1, (
        "hired_count must be 1 when one candidate is in the final positive stage"
    )
    assert pipeline.openings == 2, "openings must match the vacancy.openings field"


async def test_vacancy_stages_returns_ordered_stages(session: AsyncSession) -> None:
    """PipelineRepository.get_pipeline().stages powers the /vacancies/{id}/stages endpoint.

    Verifies that stage names resolve from org.parameters and are ordered correctly,
    and that client company data is NOT present (safe for candidate-portal tokens).
    """
    stage_param_a = await BaseRepository(session, Parameter).add(
        Parameter(type="stage", code=uuid.uuid4().hex[:8], name="Revisión CV")
    )
    stage_param_b = await BaseRepository(session, Parameter).add(
        Parameter(type="stage", code=uuid.uuid4().hex[:8], name="Entrevista Final")
    )
    company = await BaseRepository(session, ClientCompany).add(ClientCompany(name="ConfidentialCo"))
    contact = await BaseRepository(session, Contact).add(
        Contact(client_company_id=company.id, first_name="X", last_name="Y", email="x@y.co")
    )
    dept = await BaseRepository(session, Department).add(Department(name="Ops"))
    process = await BaseRepository(session, Process).add(
        Process(
            client_company_id=company.id, department_id=dept.id, name=f"P{uuid.uuid4().hex[:6]}"
        )
    )
    await BaseRepository(session, ProcessStage).add(
        ProcessStage(
            process_id=process.id, stage_id=stage_param_a.id, order=1, is_final_positive=False
        )
    )
    await BaseRepository(session, ProcessStage).add(
        ProcessStage(
            process_id=process.id, stage_id=stage_param_b.id, order=2, is_final_positive=True
        )
    )
    vacancy = await BaseRepository(session, Vacancy).add(
        Vacancy(
            vacancy_name_id=stage_param_a.id,
            client_company_id=company.id,
            contact_id=contact.id,
            department_id=dept.id,
            process_id=process.id,
            career_id=stage_param_a.id,
            city_id=stage_param_a.id,
            work_mode_id=stage_param_a.id,
            resource_level_id=stage_param_a.id,
            status_id=stage_param_a.id,
        )
    )

    pipeline = await PipelineRepository(session).get_pipeline(vacancy.id)
    stages = pipeline.stages

    assert len(stages) == 2
    assert stages[0].order == 1
    assert stages[0].name == "Revisión CV"
    assert stages[0].is_final_positive is False
    assert stages[1].order == 2
    assert stages[1].name == "Entrevista Final"
    assert stages[1].is_final_positive is True
    # Confirm no client identity is exposed (StageRow has no client fields)
    assert not hasattr(stages[0], "client_company")
    assert not hasattr(stages[0], "contact")


async def _vacancy_with_reserved_final_stage(session: AsyncSession) -> Vacancy:
    """Vacancy whose process has Postulantes (order=1) + Contratados at the
    reserved sort order FINAL_STAGE_ORDER (9999), mirroring what the backend
    seeds so the endpoints must translate the sort key into a display position.
    """
    initial = await BaseRepository(session, Parameter).add(
        Parameter(type="stage", code=uuid.uuid4().hex[:8], name="Postulantes")
    )
    final = await BaseRepository(session, Parameter).add(
        Parameter(type="stage", code=uuid.uuid4().hex[:8], name="Contratados")
    )
    company = await BaseRepository(session, ClientCompany).add(ClientCompany(name="ReservedCo"))
    contact = await BaseRepository(session, Contact).add(
        Contact(client_company_id=company.id, first_name="R", last_name="C", email="r@c.co")
    )
    dept = await BaseRepository(session, Department).add(Department(name="Sales"))
    process = await BaseRepository(session, Process).add(
        Process(
            client_company_id=company.id,
            department_id=dept.id,
            name=f"P{uuid.uuid4().hex[:6]}",
        )
    )
    await BaseRepository(session, ProcessStage).add(
        ProcessStage(process_id=process.id, stage_id=initial.id, order=1, is_initial=True)
    )
    await BaseRepository(session, ProcessStage).add(
        ProcessStage(process_id=process.id, stage_id=final.id, order=9999, is_final_positive=True)
    )
    return await BaseRepository(session, Vacancy).add(
        Vacancy(
            vacancy_name_id=initial.id,
            client_company_id=company.id,
            contact_id=contact.id,
            department_id=dept.id,
            process_id=process.id,
            career_id=initial.id,
            city_id=initial.id,
            work_mode_id=initial.id,
            resource_level_id=initial.id,
            status_id=initial.id,
        )
    )


async def test_pipeline_endpoint_exposes_sequential_display_order_not_reserved_9999(
    session: AsyncSession,
) -> None:
    """The reserved 9999 sort order must never reach the UI: the pipeline
    endpoint exposes a sequential 1..N position, and the virtual Rechazados
    column stays last.
    """
    from app.modules.recruitment.api.vacancies_routes import get_vacancy_pipeline

    vacancy = await _vacancy_with_reserved_final_stage(session)
    result = await get_vacancy_pipeline(vacancy.id, session, ACTOR)

    assert [s.name for s in result.stages] == ["Postulantes", "Contratados", "Rechazados"]
    assert [s.order for s in result.stages] == [1, 2, 3]
    # Contratados must show its sequential position, not the reserved sort key.
    contratados = next(s for s in result.stages if s.name == "Contratados")
    assert contratados.order == 2
    assert contratados.type == "final"


async def test_vacancy_stages_endpoint_exposes_sequential_display_order(
    session: AsyncSession,
) -> None:
    """The candidate-portal /stages endpoint must also translate the reserved
    9999 order into a sequential position so the progress badge reads 2, not 9999.
    """
    from app.modules.recruitment.api.vacancies_routes import get_vacancy_stages

    vacancy = await _vacancy_with_reserved_final_stage(session)
    items = await get_vacancy_stages(vacancy.id, session)

    assert [i.order for i in items] == [1, 2]
    assert items[1].name == "Contratados"
