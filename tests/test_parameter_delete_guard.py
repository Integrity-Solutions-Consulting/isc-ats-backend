"""Referential delete guard for the org.parameters catalog.

A catalog parameter cannot be soft-deleted while an active row anywhere in the
system still references it (vacancy, candidate, application, process stage, ...).
Soft-deleted dependents do not block deletion.
"""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.org.application.parameters_service import (
    ParameterInUseError,
    ParameterService,
)
from app.modules.org.infrastructure.models import (
    ClientCompany,
    Contact,
    Department,
    Parameter,
    Process,
    ProcessStage,
)
from app.modules.org.infrastructure.parameter_usage_repository import (
    ParameterUsageRepository,
)
from app.modules.org.infrastructure.parameters_repository import ParameterRepository
from app.modules.recruitment.infrastructure.models import Vacancy
from app.shared.repository import BaseRepository


def _service(session: AsyncSession) -> ParameterService:
    usage = ParameterUsageRepository(session)
    return ParameterService(
        ParameterRepository(session), in_use_checker=usage.is_referenced
    )


async def _param(session: AsyncSession) -> Parameter:
    return await BaseRepository(session, Parameter).add(
        Parameter(type="x", code=uuid.uuid4().hex[:8], name="P")
    )


async def _vacancy_referencing(session: AsyncSession, param: Parameter) -> Vacancy:
    """A vacancy that uses `param` for every catalog FK it requires."""
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
        )
    )


async def test_delete_blocked_when_referenced_by_active_vacancy(session: AsyncSession) -> None:
    param = await _param(session)
    await _vacancy_referencing(session, param)
    with pytest.raises(ParameterInUseError):
        await _service(session).delete(param.id)


async def test_delete_blocked_when_referenced_by_process_stage(session: AsyncSession) -> None:
    param = await _param(session)
    company = await BaseRepository(session, ClientCompany).add(ClientCompany(name="Co"))
    dept = await BaseRepository(session, Department).add(Department(name="Eng"))
    process = await BaseRepository(session, Process).add(
        Process(
            client_company_id=company.id,
            department_id=dept.id,
            name=f"P{uuid.uuid4().hex[:6]}",
        )
    )
    await BaseRepository(session, ProcessStage).add(
        ProcessStage(process_id=process.id, stage_id=param.id, order=1)
    )
    with pytest.raises(ParameterInUseError):
        await _service(session).delete(param.id)


async def test_delete_allowed_when_unreferenced(session: AsyncSession) -> None:
    param = await _param(session)
    await _service(session).delete(param.id)
    refreshed = await session.get(Parameter, param.id)
    assert refreshed is not None and refreshed.is_active is False


async def test_delete_allowed_when_only_referenced_by_inactive_row(session: AsyncSession) -> None:
    param = await _param(session)
    vacancy = await _vacancy_referencing(session, param)
    await BaseRepository(session, Vacancy).soft_delete(vacancy)
    await _service(session).delete(param.id)
    refreshed = await session.get(Parameter, param.id)
    assert refreshed is not None and refreshed.is_active is False
