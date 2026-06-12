from typing import Any

from app.core.dependencies import CurrentUser
from app.modules.org.infrastructure.models import (
    ClientCompany,
    Contact,
    Department,
    Parameter,
    Process,
    ProfileTemplate,
)
from app.modules.recruitment.api.vacancies_schemas import (
    VacancyCreate,
    VacancyUpdate,
)
from app.modules.recruitment.infrastructure.models import Vacancy
from app.shared.pagination import PageParams
from app.shared.repository import BaseRepository


class VacancyNotFoundError(Exception):
    pass


class VacancyReferenceError(Exception):
    """A referenced catalog row or org entity does not exist (or is inactive)."""


class VacancyService:
    """CRUD for recruitment.vacancies, validating its many references up front.

    Each FK is checked against the live catalogs so the API answers a clear 422
    naming the offending reference, instead of an opaque integrity violation.
    """

    def __init__(
        self,
        repository: BaseRepository[Vacancy],
        parameters: BaseRepository[Parameter],
        client_companies: BaseRepository[ClientCompany],
        contacts: BaseRepository[Contact],
        departments: BaseRepository[Department],
        processes: BaseRepository[Process],
        profile_templates: BaseRepository[ProfileTemplate],
    ) -> None:
        self.repository = repository
        self.parameters = parameters
        self.client_companies = client_companies
        self.contacts = contacts
        self.departments = departments
        self.processes = processes
        self.profile_templates = profile_templates

    async def list(
        self,
        params: PageParams,
        *,
        client_company_id: int | None = None,
        status_id: int | None = None,
        department_id: int | None = None,
    ) -> tuple[list[Vacancy], int]:
        filters = {
            k: v
            for k, v in {
                "client_company_id": client_company_id,
                "status_id": status_id,
                "department_id": department_id,
            }.items()
            if v is not None
        }
        return await self.repository.list(params, filters=filters or None)

    async def get(self, vacancy_id: int) -> Vacancy:
        vacancy = await self.repository.get(vacancy_id, include_inactive=True)
        if vacancy is None:
            raise VacancyNotFoundError(f"Vacancy {vacancy_id} not found")
        return vacancy

    async def create(self, data: VacancyCreate, actor: CurrentUser) -> Vacancy:
        await self._validate_refs(data.model_dump())
        vacancy = Vacancy(
            **data.model_dump(),
            created_by=actor.user_id,
            ip_created=actor.ip,
        )
        return await self.repository.add(vacancy)

    async def update(
        self, vacancy_id: int, data: VacancyUpdate, actor: CurrentUser
    ) -> Vacancy:
        vacancy = await self.get(vacancy_id)
        changes = data.model_dump(exclude_unset=True)
        await self._validate_refs(changes)
        changes["updated_by"] = actor.user_id
        changes["ip_updated"] = actor.ip
        return await self.repository.update(vacancy, changes)

    async def delete(self, vacancy_id: int) -> None:
        vacancy = await self.get(vacancy_id)
        await self.repository.soft_delete(vacancy)

    async def _validate_refs(self, values: dict[str, Any]) -> None:
        """Validate any FK present in `values` against its catalog."""
        param_fields = (
            "vacancy_name_id",
            "career_id",
            "city_id",
            "work_mode_id",
            "resource_level_id",
            "status_id",
        )
        for field in param_fields:
            await self._assert(self.parameters, values, field)
        await self._assert(self.client_companies, values, "client_company_id")
        await self._assert(self.contacts, values, "contact_id")
        await self._assert(self.departments, values, "department_id")
        await self._assert(self.processes, values, "process_id")
        await self._assert(self.profile_templates, values, "profile_template_id")

    async def _assert(
        self, repo: BaseRepository[Any], values: dict[str, Any], field: str
    ) -> None:
        entity_id = values.get(field)
        if entity_id is not None and await repo.get(entity_id) is None:
            raise VacancyReferenceError(f"{field}={entity_id} not found")
