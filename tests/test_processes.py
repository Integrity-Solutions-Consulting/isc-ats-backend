import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import CurrentUser
from app.modules.org.api.processes_schemas import ProcessCreate
from app.modules.org.application.processes_service import (
    DuplicateProcessError,
    ProcessReferenceError,
    ProcessService,
)
from app.modules.org.infrastructure.models import ClientCompany, Department
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
