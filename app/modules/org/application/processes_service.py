from app.core.dependencies import CurrentUser
from app.modules.org.api.processes_schemas import ProcessCreate, ProcessUpdate
from app.modules.org.infrastructure.models import (
    ClientCompany,
    Department,
    Process,
    ProcessStage,
)
from app.modules.org.infrastructure.parameters_repository import ParameterRepository
from app.modules.org.infrastructure.process_stages_repository import (
    ProcessStageRepository,
)
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
    """Process CRUD with double FK validation + composite-uniqueness checks.

    On create(), two fixed stages are auto-seeded in the same transaction:
    - Postulantes (order=1, is_initial=True)  — param (stage, applicants)
    - Contratación (order=2, is_final_positive=True) — param (stage, offer)

    Both params must already exist in org.parameters (seeded via migration
    b8c9d0e1f2a3). If either is missing ProcessReferenceError is raised.
    """

    def __init__(
        self,
        repository: ProcessRepository,
        companies: BaseRepository[ClientCompany],
        departments: BaseRepository[Department],
        in_use_checker: InUseChecker | None = None,
        *,
        stage_repository: ProcessStageRepository | None = None,
        parameter_repository: ParameterRepository | None = None,
    ) -> None:
        self.repository = repository
        self.companies = companies
        self.departments = departments
        self.in_use_checker = in_use_checker
        self.stage_repository = stage_repository
        self.parameter_repository = parameter_repository

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
        return await self.repository.list(
            params, filters=filters or None, include_inactive=include_inactive
        )

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
        process = await self.repository.add(process)

        if self.stage_repository is not None and self.parameter_repository is not None:
            await self._seed_fixed_stages(process.id, actor)

        return process

    async def _seed_fixed_stages(self, process_id: int, actor: CurrentUser) -> None:
        """Insert Postulantes (order=1) and Contratación (order=2) in the same transaction."""
        applicants_param = await self.parameter_repository.get_by_type_and_code(  # type: ignore[union-attr]
            "stage", "applicants"
        )
        if applicants_param is None:
            raise ProcessReferenceError(
                "Stage parameter 'applicants' not found in org.parameters. "
                "Run migration b8c9d0e1f2a3 to seed it."
            )

        offer_param = await self.parameter_repository.get_by_type_and_code(  # type: ignore[union-attr]
            "stage", "offer"
        )
        if offer_param is None:
            raise ProcessReferenceError(
                "Stage parameter 'offer' not found in org.parameters. "
                "Run migration c2d3e4f5a6b7 to seed it."
            )

        await self.stage_repository.add(  # type: ignore[union-attr]
            ProcessStage(
                process_id=process_id,
                stage_id=applicants_param.id,
                order=1,
                is_initial=True,
                is_final_positive=False,
                created_by=actor.user_id,
                ip_created=actor.ip,
            )
        )
        await self.stage_repository.add(  # type: ignore[union-attr]
            ProcessStage(
                process_id=process_id,
                stage_id=offer_param.id,
                order=2,
                is_initial=False,
                is_final_positive=True,
                created_by=actor.user_id,
                ip_created=actor.ip,
            )
        )

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
