from app.core.dependencies import CurrentUser
from app.modules.org.api.departments_schemas import DepartmentCreate, DepartmentUpdate
from app.modules.org.infrastructure.models import Department
from app.shared.pagination import PageParams
from app.shared.repository import BaseRepository


class DepartmentNotFoundError(Exception):
    pass


class DepartmentService:
    """Thin CRUD service for the org.departments catalog (ORM is the model)."""

    def __init__(self, repository: BaseRepository[Department]) -> None:
        self.repository = repository

    async def list(self, params: PageParams, *, include_inactive: bool = False) -> tuple[list[Department], int]:
        return await self.repository.list(params, include_inactive=include_inactive)

    async def get(self, department_id: int) -> Department:
        department = await self.repository.get(department_id, include_inactive=True)
        if department is None:
            raise DepartmentNotFoundError(f"Department {department_id} not found")
        return department

    async def create(self, data: DepartmentCreate, actor: CurrentUser) -> Department:
        department = Department(
            name=data.name,
            description=data.description,
            created_by=actor.user_id,
            ip_created=actor.ip,
        )
        return await self.repository.add(department)

    async def update(
        self, department_id: int, data: DepartmentUpdate, actor: CurrentUser
    ) -> Department:
        department = await self.get(department_id)
        changes = data.model_dump(exclude_unset=True)
        changes["updated_by"] = actor.user_id
        changes["ip_updated"] = actor.ip
        return await self.repository.update(department, changes)

    async def delete(self, department_id: int) -> None:
        department = await self.get(department_id)
        await self.repository.soft_delete(department)
