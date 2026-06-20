from app.core.dependencies import CurrentUser
from app.modules.org.api.profile_templates_schemas import (
    ProfileTemplateCreate,
    ProfileTemplateUpdate,
)
from app.modules.org.infrastructure.models import ProfileTemplate
from app.shared.pagination import PageParams
from app.shared.ports import InUseChecker
from app.shared.repository import BaseRepository


class ProfileTemplateNotFoundError(Exception):
    pass


class ProfileTemplateInUseError(Exception):
    """Cannot delete a profile template referenced by a live (non-closed) vacancy."""


class ProfileTemplateService:
    """Profile-template CRUD. Templates are universal — no client or department."""

    def __init__(
        self,
        repository: BaseRepository[ProfileTemplate],
        in_use_checker: InUseChecker | None = None,
    ) -> None:
        self.repository = repository
        self.in_use_checker = in_use_checker

    async def list(self, params: PageParams) -> tuple[list[ProfileTemplate], int]:
        return await self.repository.list(params, include_inactive=True)

    async def get(self, template_id: int) -> ProfileTemplate:
        template = await self.repository.get(template_id, include_inactive=True)
        if template is None:
            raise ProfileTemplateNotFoundError(f"ProfileTemplate {template_id} not found")
        return template

    async def create(
        self, data: ProfileTemplateCreate, actor: CurrentUser
    ) -> ProfileTemplate:
        template = ProfileTemplate(
            name=data.name,
            created_by=actor.user_id,
            ip_created=actor.ip,
        )
        return await self.repository.add(template)

    async def update(
        self, template_id: int, data: ProfileTemplateUpdate, actor: CurrentUser
    ) -> ProfileTemplate:
        template = await self.get(template_id)
        changes = data.model_dump(exclude_unset=True)
        changes["updated_by"] = actor.user_id
        changes["ip_updated"] = actor.ip
        return await self.repository.update(template, changes)

    async def delete(self, template_id: int) -> None:
        template = await self.get(template_id)
        if self.in_use_checker is not None and await self.in_use_checker(template_id):
            raise ProfileTemplateInUseError(
                "No se puede eliminar la plantilla: está en uso por una vacante activa."
            )
        await self.repository.soft_delete(template)
