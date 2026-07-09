from sqlalchemy import select

from app.core.dependencies import CurrentUser
from app.modules.org.api.profile_templates_schemas import (
    ProfileTemplateCreate,
    ProfileTemplateUpdate,
)
from app.modules.org.infrastructure.models import ProfileTemplate, ProfileTemplateItem
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
        items_repository: BaseRepository[ProfileTemplateItem] | None = None,
    ) -> None:
        self.repository = repository
        self.in_use_checker = in_use_checker
        self.items_repository = items_repository

    async def list(self, params: PageParams) -> tuple[list[ProfileTemplate], int]:
        return await self.repository.list(params, include_inactive=True)

    async def get(self, template_id: int) -> ProfileTemplate:
        template = await self.repository.get(template_id, include_inactive=True)
        if template is None:
            raise ProfileTemplateNotFoundError(f"ProfileTemplate {template_id} not found")
        return template

    async def create(self, data: ProfileTemplateCreate, actor: CurrentUser) -> ProfileTemplate:
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

    async def copy(self, template_id: int, actor: CurrentUser) -> ProfileTemplate:
        """Deep-copy a template and its items into brand-new rows.

        The copy is fully independent from the source: a new `ProfileTemplate`
        row and, for each source item, a new `ProfileTemplateItem` row pointing
        at the copy. Nothing is shared — mutating the copy never touches the
        source.
        """
        source = await self.get(template_id)
        copy = await self.repository.add(
            ProfileTemplate(
                name=f"{source.name} (copia)",
                created_by=actor.user_id,
                ip_created=actor.ip,
            )
        )

        if self.items_repository is not None:
            stmt = select(ProfileTemplateItem).where(
                ProfileTemplateItem.template_id == template_id,
                ProfileTemplateItem.is_active.is_(True),
            )
            source_items = (await self.items_repository.session.execute(stmt)).scalars().all()
            for item in source_items:
                await self.items_repository.add(
                    ProfileTemplateItem(
                        template_id=copy.id,
                        category_id=item.category_id,
                        name=item.name,
                        created_by=actor.user_id,
                        ip_created=actor.ip,
                    )
                )

        return copy

    async def delete(self, template_id: int) -> None:
        template = await self.get(template_id)
        if self.in_use_checker is not None and await self.in_use_checker(template_id):
            raise ProfileTemplateInUseError(
                "No se puede eliminar la plantilla: está en uso por una vacante activa."
            )
        await self.repository.soft_delete(template)
