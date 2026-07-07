import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import CurrentUser
from app.modules.org.api.process_stages_schemas import ProcessStageCreate
from app.modules.org.api.processes_schemas import ProcessCreate
from app.modules.org.application.process_stages_service import ProcessStageService
from app.modules.org.application.processes_service import (
    FINAL_STAGE_ORDER,
    DuplicateProcessError,
    ProcessReferenceError,
    ProcessService,
)
from app.modules.org.infrastructure.models import (
    ClientCompany,
    Department,
    Parameter,
    Process,
)
from app.modules.org.infrastructure.parameters_repository import ParameterRepository
from app.modules.org.infrastructure.process_stages_repository import (
    ProcessStageRepository,
)
from app.modules.org.infrastructure.processes_repository import ProcessRepository
from app.shared.repository import BaseRepository

ACTOR = CurrentUser(user_id=1, ip="127.0.0.1")


def _service(session: AsyncSession) -> ProcessService:
    return ProcessService(
        ProcessRepository(session),
        BaseRepository(session, ClientCompany),
        BaseRepository(session, Department),
    )


async def _company_and_dept(session: AsyncSession) -> tuple[int, int]:
    company = await BaseRepository(session, ClientCompany).add(ClientCompany(name="ACME"))
    dept = await BaseRepository(session, Department).add(Department(name="Tech"))
    return company.id, dept.id


async def test_create_process_rejects_missing_references(session: AsyncSession) -> None:
    data = ProcessCreate(client_company_id=999, department_id=999, name="X")
    with pytest.raises(ProcessReferenceError):
        await _service(session).create(data, ACTOR)


async def test_create_process_rejects_duplicate(session: AsyncSession) -> None:
    company_id, dept_id = await _company_and_dept(session)
    service = _service(session)
    data = ProcessCreate(
        client_company_id=company_id, department_id=dept_id, name="Backend"
    )
    await service.create(data, ACTOR)

    with pytest.raises(DuplicateProcessError):
        await service.create(data, ACTOR)


async def _ensure_stage_param(session: AsyncSession, code: str, name: str) -> Parameter:
    existing = await ParameterRepository(session).get_by_type_and_code("stage", code)
    if existing is not None:
        return existing
    return await BaseRepository(session, Parameter).add(
        Parameter(type="stage", code=code, name=name)
    )


def _service_with_seeding(session: AsyncSession) -> ProcessService:
    return ProcessService(
        ProcessRepository(session),
        BaseRepository(session, ClientCompany),
        BaseRepository(session, Department),
        stage_repository=ProcessStageRepository(session),
        parameter_repository=ParameterRepository(session),
    )


async def test_create_process_seeds_final_stage_last_leaving_order_2_free(
    session: AsyncSession,
) -> None:
    # The final backbone stage (Contratados) must be auto-seeded at the reserved
    # high order — not at 2 — so a custom stage can sit between Postulantes and it.
    await _ensure_stage_param(session, "applicants", "Postulantes")
    await _ensure_stage_param(session, "offer", "Contratados")
    company_id, dept_id = await _company_and_dept(session)

    process = await _service_with_seeding(session).create(
        ProcessCreate(client_company_id=company_id, department_id=dept_id, name="Backend"),
        ACTOR,
    )

    stages = await ProcessStageRepository(session).list_by_process(process.id)
    initial = next(s for s in stages if s.is_initial)
    final = next(s for s in stages if s.is_final_positive)
    assert initial.order == 1
    assert final.order == FINAL_STAGE_ORDER
    assert 2 not in {s.order for s in stages}  # order 2 is free

    # A custom stage can now be added at order 2 without clashing with the final.
    custom = await _ensure_stage_param(session, "interview_x", "Entrevista X")
    added = await ProcessStageService(
        ProcessStageRepository(session),
        BaseRepository(session, Process),
        BaseRepository(session, Parameter),
    ).create(
        ProcessStageCreate(process_id=process.id, stage_id=custom.id, order=2),
        ACTOR,
    )
    assert added.order == 2
