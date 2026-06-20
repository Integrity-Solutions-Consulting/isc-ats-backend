from app.core.dependencies import CurrentUser
from app.modules.org.api.processes_schemas import ProcessCreate, ProcessUpdate
from app.modules.org.infrastructure.models import ClientCompany, Department, Process
from app.modules.org.infrastructure.processes_repository import ProcessRepository
from app.shared.pagination import PageParams
from app.shared.ports import InUseChecker
from app.shared.repository import BaseRepository


class ProcessNotFoundError(Exception):
    pass


class ProcessReferenceError(Exception):
    """A referenced client_company or department does not exist."""


class DuplicateProcessError(Exception):
    """A process with the same (company, department, name) already exists."""


class ProcessInUseError(Exception):
    """Cannot delete a process referenced by a live (non-closed) vacancy."""


class ProcessService:
    """Process CRUD with double FK validation + composite-uniqueness checks."""

    def __init__(
        self,
        repository: ProcessRepository,
        companies: BaseRepository[ClientCompany],
        departments: BaseRepository[Department],
        in_use_checker: InUseChecker | None = None,
    ) -> None:
        self.repository = repository
        self.companies = companies
        self.departments = departments
        self.in_use_checker = in_use_checker

    async def list(
        self,
        params: PageParams,
        *,
        client_company_id: int | None = None,
        department_id: int | None = None,
        include_inactive: bool = False,
    ) -> tuple[list[Process], int]:
        filters = {
            k: v
            for k, v in {
                "client_company_id": client_company_id,
                "department_id": department_id,
            }.items()
            if v is not None
        }
        return await self.repository.list(params, filters=filters or None, include_inactive=include_inactive)

    async def get(self, process_id: int) -> Process:
        process = await self.repository.get(process_id, include_inactive=True)
        if process is None:
            raise ProcessNotFoundError(f"Process {process_id} not found")
        return process

    async def _assert_references(self, client_company_id: int, department_id: int) -> None:
        if await self.companies.get(client_company_id) is None:
            raise ProcessReferenceError(f"ClientCompany {client_company_id} not found")
        if await self.departments.get(department_id) is None:
            raise ProcessReferenceError(f"Department {department_id} not found")

    async def _assert_unique(
        self,
        client_company_id: int,
        department_id: int,
        name: str,
        *,
        exclude_id: int | None = None,
    ) -> None:
        dup = await self.repository.find_duplicate(
            client_company_id, department_id, name, exclude_id=exclude_id
        )
        if dup is not None:
            raise DuplicateProcessError(
                f"Process '{name}' already exists for this company and department"
            )

    async def create(self, data: ProcessCreate, actor: CurrentUser) -> Process:
        await self._assert_references(data.client_company_id, data.department_id)
        await self._assert_unique(data.client_company_id, data.department_id, data.name)
        process = Process(
            client_company_id=data.client_company_id,
            department_id=data.department_id,
            name=data.name,
            description=data.description,
            created_by=actor.user_id,
            ip_created=actor.ip,
        )
        return await self.repository.add(process)

    async def update(
        self, process_id: int, data: ProcessUpdate, actor: CurrentUser
    ) -> Process:
        process = await self.get(process_id)
        changes = data.model_dump(exclude_unset=True)

        company_id = changes.get("client_company_id", process.client_company_id)
        department_id = changes.get("department_id", process.department_id)
        name = changes.get("name", process.name)

        if "client_company_id" in changes or "department_id" in changes:
            await self._assert_references(company_id, department_id)
        if {"client_company_id", "department_id", "name"} & changes.keys():
            await self._assert_unique(
                company_id, department_id, name, exclude_id=process.id
            )

        changes["updated_by"] = actor.user_id
        changes["ip_updated"] = actor.ip
        return await self.repository.update(process, changes)

    async def delete(self, process_id: int) -> None:
        process = await self.get(process_id)
        if self.in_use_checker is not None and await self.in_use_checker(process_id):
            raise ProcessInUseError(
                "No se puede eliminar el proceso: está en uso por una vacante activa."
            )
        await self.repository.soft_delete(process)
