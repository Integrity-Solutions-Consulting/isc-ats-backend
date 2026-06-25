"""U1 — is_initial column tests (TDD-first, Slice 1 / process-stage-flow).

Tests cover:
- ProcessStage.is_initial attribute exists, defaults False.
- Column persists round-trip (flush → refresh).
- Seed param (type='stage', code='applicants') present in DB after migration.
- 'offer' parameter renamed to 'Contratación'.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.org.infrastructure.models import (
    ClientCompany,
    Department,
    Parameter,
    Process,
    ProcessStage,
)
from app.modules.org.infrastructure.parameters_repository import ParameterRepository
from app.shared.repository import BaseRepository

# ---------------------------------------------------------------------------
# Model-level: is_initial attribute
# ---------------------------------------------------------------------------


async def test_process_stage_is_initial_defaults_false(session: AsyncSession) -> None:
    """ProcessStage.is_initial must exist and default to False."""
    company = await BaseRepository(session, ClientCompany).add(
        ClientCompany(name=f"Co{uuid.uuid4().hex[:6]}", created_by=1)
    )
    dept = await BaseRepository(session, Department).add(
        Department(name=f"D{uuid.uuid4().hex[:6]}", created_by=1)
    )
    process = await BaseRepository(session, Process).add(
        Process(
            client_company_id=company.id,
            department_id=dept.id,
            name=f"Proc{uuid.uuid4().hex[:6]}",
            created_by=1,
        )
    )
    param = await BaseRepository(session, Parameter).add(
        Parameter(type="stage", code=uuid.uuid4().hex[:8], name="Screening", created_by=1)
    )
    stage = await BaseRepository(session, ProcessStage).add(
        ProcessStage(
            process_id=process.id,
            stage_id=param.id,
            order=1,
            created_by=1,
        )
    )
    assert hasattr(stage, "is_initial"), "ProcessStage must have is_initial attribute"
    assert stage.is_initial is False, "is_initial must default to False"


async def test_process_stage_is_initial_persists_true(session: AsyncSession) -> None:
    """is_initial=True must persist through flush → refresh cycle."""
    company = await BaseRepository(session, ClientCompany).add(
        ClientCompany(name=f"Co{uuid.uuid4().hex[:6]}", created_by=1)
    )
    dept = await BaseRepository(session, Department).add(
        Department(name=f"D{uuid.uuid4().hex[:6]}", created_by=1)
    )
    process = await BaseRepository(session, Process).add(
        Process(
            client_company_id=company.id,
            department_id=dept.id,
            name=f"Proc{uuid.uuid4().hex[:6]}",
            created_by=1,
        )
    )
    param = await BaseRepository(session, Parameter).add(
        Parameter(type="stage", code=uuid.uuid4().hex[:8], name="Postulantes", created_by=1)
    )
    stage = await BaseRepository(session, ProcessStage).add(
        ProcessStage(
            process_id=process.id,
            stage_id=param.id,
            order=1,
            is_initial=True,
            created_by=1,
        )
    )
    assert stage.is_initial is True, "is_initial=True must persist"


# ---------------------------------------------------------------------------
# Seed verification: migration must have inserted the 'applicants' parameter
# ---------------------------------------------------------------------------


async def test_applicants_stage_param_exists(session: AsyncSession) -> None:
    """Migration must have seeded (type='stage', code='applicants') in org.parameters."""
    param = await ParameterRepository(session).get_by_type_and_code("stage", "applicants")
    assert param is not None, "Parameter (stage, applicants) must exist after migration"
    assert param.name == "Postulantes", f"Expected 'Postulantes', got '{param.name}'"
    assert param.is_active is True


async def test_offer_param_renamed_to_contratacion(session: AsyncSession) -> None:
    """Migration must have renamed (stage, offer) from 'Oferta · Contratación' to 'Contratación'."""
    param = await ParameterRepository(session).get_by_type_and_code("stage", "offer")
    assert param is not None, "Parameter (stage, offer) must exist"
    assert param.name == "Contratación", f"Expected 'Contratación', got '{param.name}'"
