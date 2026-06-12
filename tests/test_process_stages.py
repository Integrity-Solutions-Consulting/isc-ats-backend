import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import CurrentUser
from app.modules.org.api.process_stages_schemas import ProcessStageCreate
from app.modules.org.application.process_stages_service import (
    DuplicateStageError,
    ProcessStageReferenceError,
    ProcessStageService,
)
from app.modules.org.infrastructure.models import (
    ClientCompany,
    Department,
    Parameter,
    Process,
)
from app.modules.org.infrastructure.process_stages_repository import (
    ProcessStageRepository,
)
from app.shared.repository import BaseRepository

ACTOR = CurrentUser(user_id=1, ip="127.0.0.1")


def _service(session: AsyncSession) -> ProcessStageService:
    return ProcessStageService(
        ProcessStageRepository(session),
        BaseRepository(session, Process),
        BaseRepository(session, Parameter),
    )


async def _fixtures(session: AsyncSession) -> tuple[int, int, int]:
    """Returns (process_id, stage_param_id, non_stage_param_id)."""
    company = await BaseRepository(session, ClientCompany).add(ClientCompany(name="ACME"))
    dept = await BaseRepository(session, Department).add(Department(name="Tech"))
    process = await BaseRepository(session, Process).add(
        Process(client_company_id=company.id, department_id=dept.id, name="Backend")
    )
    stage = await BaseRepository(session, Parameter).add(
        Parameter(type="stage", code=uuid.uuid4().hex[:8], name="Screening")
    )
    other = await BaseRepository(session, Parameter).add(
        Parameter(type="city", code=uuid.uuid4().hex[:8], name="Quito")
    )
    return process.id, stage.id, other.id


async def test_stage_id_must_be_a_stage_parameter(session: AsyncSession) -> None:
    process_id, _, non_stage_id = await _fixtures(session)
    data = ProcessStageCreate(process_id=process_id, stage_id=non_stage_id, order=1)
    with pytest.raises(ProcessStageReferenceError):
        await _service(session).create(data, ACTOR)


async def test_duplicate_order_rejected(session: AsyncSession) -> None:
    process_id, stage_id, _ = await _fixtures(session)
    service = _service(session)
    await service.create(
        ProcessStageCreate(process_id=process_id, stage_id=stage_id, order=1), ACTOR
    )
    # Same order, different (would-be) stage → order clash.
    other_stage = await BaseRepository(session, Parameter).add(
        Parameter(type="stage", code=uuid.uuid4().hex[:8], name="Interview")
    )
    with pytest.raises(DuplicateStageError):
        await service.create(
            ProcessStageCreate(process_id=process_id, stage_id=other_stage.id, order=1),
            ACTOR,
        )


async def test_list_by_process_is_ordered(session: AsyncSession) -> None:
    process_id, stage_id, _ = await _fixtures(session)
    service = _service(session)
    third = await BaseRepository(session, Parameter).add(
        Parameter(type="stage", code=uuid.uuid4().hex[:8], name="Offer")
    )
    await service.create(
        ProcessStageCreate(process_id=process_id, stage_id=third.id, order=3), ACTOR
    )
    await service.create(
        ProcessStageCreate(process_id=process_id, stage_id=stage_id, order=1), ACTOR
    )
    stages = await service.list_by_process(process_id)
    assert [s.order for s in stages] == [1, 3]
