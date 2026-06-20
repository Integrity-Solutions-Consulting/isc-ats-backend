"""Business rule: a process cannot be deleted while a live (non-closed) vacancy
uses it. Closed/cancelled vacancies do not block deletion.
"""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import CurrentUser
from app.modules.org.application.processes_service import (
    ProcessInUseError,
    ProcessService,
)
from app.modules.org.infrastructure.models import (
    ClientCompany,
    Contact,
    Department,
    Parameter,
    Process,
)
from app.modules.org.infrastructure.parameters_repository import ParameterRepository
from app.modules.org.infrastructure.processes_repository import ProcessRepository
from app.modules.recruitment.infrastructure.models import Vacancy
from app.modules.recruitment.infrastructure.vacancy_usage_repository import (
    VacancyUsageRepository,
)
from app.shared.repository import BaseRepository

ACTOR = CurrentUser(user_id=1, ip="127.0.0.1")


def _service(session: AsyncSession) -> ProcessService:
    usage = VacancyUsageRepository(session)
    return ProcessService(
        ProcessRepository(session),
        BaseRepository(session, ClientCompany),
        BaseRepository(session, Department),
        in_use_checker=lambda pid: usage.is_referenced_by_live_vacancy("process_id", pid),
    )


async def _status(session: AsyncSession, code: str) -> Parameter:
    p = await ParameterRepository(session).get_by_type_and_code("vacancy_status", code)
    assert p is not None
    return p


async def _process_with_vacancy(session: AsyncSession, status_code: str) -> Process:
    sp = await BaseRepository(session, Parameter).add(
        Parameter(type="x", code=uuid.uuid4().hex[:8], name="P")
    )
    company = await BaseRepository(session, ClientCompany).add(ClientCompany(name="UseCo"))
    dept = await BaseRepository(session, Department).add(Department(name="Eng"))
    process = await BaseRepository(session, Process).add(
        Process(
            client_company_id=company.id,
            department_id=dept.id,
            name=f"P{uuid.uuid4().hex[:6]}",
        )
    )
    contact = await BaseRepository(session, Contact).add(
        Contact(client_company_id=company.id, first_name="C", last_name="D", email="c@d.co")
    )
    status = await _status(session, status_code)
    await BaseRepository(session, Vacancy).add(
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
            status_id=status.id,
        )
    )
    return process


async def test_delete_blocked_when_used_by_active_vacancy(session: AsyncSession) -> None:
    process = await _process_with_vacancy(session, "active")
    with pytest.raises(ProcessInUseError):
        await _service(session).delete(process.id)


async def test_delete_allowed_when_vacancy_closed(session: AsyncSession) -> None:
    process = await _process_with_vacancy(session, "closed")
    await _service(session).delete(process.id)
    refreshed = await session.get(Process, process.id)
    assert refreshed is not None and refreshed.is_active is False


async def test_delete_allowed_when_vacancy_cancelled(session: AsyncSession) -> None:
    process = await _process_with_vacancy(session, "cancelled")
    await _service(session).delete(process.id)
    refreshed = await session.get(Process, process.id)
    assert refreshed is not None and refreshed.is_active is False


async def test_delete_allowed_when_no_vacancy(session: AsyncSession) -> None:
    company = await BaseRepository(session, ClientCompany).add(ClientCompany(name="FreeCo"))
    dept = await BaseRepository(session, Department).add(Department(name="Ops"))
    process = await BaseRepository(session, Process).add(
        Process(
            client_company_id=company.id,
            department_id=dept.id,
            name=f"P{uuid.uuid4().hex[:6]}",
        )
    )
    await _service(session).delete(process.id)
    refreshed = await session.get(Process, process.id)
    assert refreshed is not None and refreshed.is_active is False
