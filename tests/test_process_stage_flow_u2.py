"""U2 — ProcessService.create() auto-seed fixed stages (TDD-first, Slice 1 / process-stage-flow).

Tests cover:
- create() inserts Postulantes (order=1, is_initial=True) in same transaction.
- create() inserts Contratación (reserved final order, is_final_positive=True) in same transaction.
- Both stages resolve param ids by code ('applicants' and 'offer').
- Raises ProcessReferenceError when 'applicants' param is missing.
- Raises ProcessReferenceError when 'offer' param is missing.
"""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import CurrentUser
from app.modules.org.api.processes_schemas import ProcessCreate
from app.modules.org.application.processes_service import (
    FINAL_STAGE_ORDER,
    ProcessReferenceError,
    ProcessService,
)
from app.modules.org.infrastructure.models import (
    ClientCompany,
    Department,
    Parameter,
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
        stage_repository=ProcessStageRepository(session),
        parameter_repository=ParameterRepository(session),
    )


async def _seed_company_and_dept(session: AsyncSession) -> tuple[int, int]:
    company = await BaseRepository(session, ClientCompany).add(
        ClientCompany(name=f"Co{uuid.uuid4().hex[:6]}", created_by=1)
    )
    dept = await BaseRepository(session, Department).add(
        Department(name=f"D{uuid.uuid4().hex[:6]}", created_by=1)
    )
    return company.id, dept.id


async def _seed_stage_params(session: AsyncSession) -> None:
    """Ensure the two fixed stage params exist for ProcessService.create().

    The migration b8c9d0e1f2a3 seeds them globally; within a rolled-back test
    session those rows are already visible (they were committed by alembic before
    tests run). We only insert if absent — which happens in the error-path tests
    that deliberately omit one of the params.
    """
    param_repo = ParameterRepository(session)
    repo = BaseRepository(session, Parameter)
    if await param_repo.get_by_type_and_code("stage", "applicants") is None:
        await repo.add(
            Parameter(type="stage", code="applicants", name="Postulantes", created_by=1)
        )
    if await param_repo.get_by_type_and_code("stage", "offer") is None:
        await repo.add(
            Parameter(type="stage", code="offer", name="Contratación", created_by=1)
        )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_create_process_inserts_postulantes_stage(session: AsyncSession) -> None:
    """ProcessService.create() must insert a Postulantes stage (order=1, is_initial=True)."""
    company_id, dept_id = await _seed_company_and_dept(session)
    await _seed_stage_params(session)

    service = _service(session)
    process = await service.create(
        ProcessCreate(client_company_id=company_id, department_id=dept_id, name="Backend"),
        ACTOR,
    )

    stages = await ProcessStageRepository(session).list_by_process(process.id)
    postulantes = next((s for s in stages if s.order == 1), None)
    assert postulantes is not None, "Postulantes stage (order=1) must be created"
    assert postulantes.is_initial is True, "Postulantes stage must have is_initial=True"

    # Confirm it references the 'applicants' param
    applicants_param = await ParameterRepository(session).get_by_type_and_code(
        "stage", "applicants"
    )
    assert applicants_param is not None
    assert postulantes.stage_id == applicants_param.id


async def test_create_process_inserts_contratacion_stage(session: AsyncSession) -> None:
    """ProcessService.create() inserts Contratación (reserved final order, is_final_positive=True)."""
    company_id, dept_id = await _seed_company_and_dept(session)
    await _seed_stage_params(session)

    service = _service(session)
    process = await service.create(
        ProcessCreate(client_company_id=company_id, department_id=dept_id, name="DevOps"),
        ACTOR,
    )

    stages = await ProcessStageRepository(session).list_by_process(process.id)
    contratacion = next((s for s in stages if s.order == FINAL_STAGE_ORDER), None)
    assert contratacion is not None, "Contratación stage (reserved final order) must be created"
    assert contratacion.is_final_positive is True, (
        "Contratación stage must have is_final_positive=True"
    )

    offer_param = await ParameterRepository(session).get_by_type_and_code("stage", "offer")
    assert offer_param is not None
    assert contratacion.stage_id == offer_param.id


async def test_create_process_both_stages_in_same_transaction(session: AsyncSession) -> None:
    """Both fixed stages (Postulantes + Contratación) must exist after create()."""
    company_id, dept_id = await _seed_company_and_dept(session)
    await _seed_stage_params(session)

    service = _service(session)
    process = await service.create(
        ProcessCreate(client_company_id=company_id, department_id=dept_id, name="QA"),
        ACTOR,
    )

    stages = await ProcessStageRepository(session).list_by_process(process.id)
    orders = sorted(s.order for s in stages)
    assert orders == [1, FINAL_STAGE_ORDER], f"Expected orders [1, {FINAL_STAGE_ORDER}], got {orders}"


# ---------------------------------------------------------------------------
# Error cases: missing stage params — use a stub ParameterRepository
# ---------------------------------------------------------------------------
# The migration b8c9d0e1f2a3 has already committed 'applicants' and 'offer'
# to the live DB. We cannot hide those rows in a rolled-back session, so we
# test the error branches by injecting a stub ParameterRepository that always
# returns None, simulating a DB where the param is absent.


class _StubParameterRepo:
    """Stub ParameterRepository that returns None for the nominated missing code
    and delegates to a real repo for any other code."""

    def __init__(self, real_repo: ParameterRepository, missing_code: str) -> None:
        self._real = real_repo
        self._missing = missing_code

    async def get_by_type_and_code(self, type_: str, code: str) -> object:
        if code == self._missing:
            return None
        return await self._real.get_by_type_and_code(type_, code)


def _service_with_stub_params(
    session: AsyncSession, missing_code: str
) -> ProcessService:
    return ProcessService(
        ProcessRepository(session),
        BaseRepository(session, ClientCompany),
        BaseRepository(session, Department),
        stage_repository=ProcessStageRepository(session),
        parameter_repository=_StubParameterRepo(  # type: ignore[arg-type]
            ParameterRepository(session), missing_code
        ),
    )


async def test_create_raises_when_applicants_param_missing(session: AsyncSession) -> None:
    """ProcessService.create() must raise ProcessReferenceError when 'applicants' param absent."""
    company_id, dept_id = await _seed_company_and_dept(session)
    with pytest.raises(ProcessReferenceError):
        await _service_with_stub_params(session, "applicants").create(
            ProcessCreate(client_company_id=company_id, department_id=dept_id, name="SRE"),
            ACTOR,
        )


async def test_create_raises_when_offer_param_missing(session: AsyncSession) -> None:
    """ProcessService.create() must raise ProcessReferenceError when 'offer' param absent."""
    company_id, dept_id = await _seed_company_and_dept(session)
    with pytest.raises(ProcessReferenceError):
        await _service_with_stub_params(session, "offer").create(
            ProcessCreate(client_company_id=company_id, department_id=dept_id, name="Data"),
            ACTOR,
        )
