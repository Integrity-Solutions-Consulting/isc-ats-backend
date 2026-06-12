from app.core.dependencies import CurrentUser
from app.modules.org.api.profile_template_items_schemas import (
    ProfileTemplateItemCreate,
    ProfileTemplateItemUpdate,
)
from app.modules.org.infrastructure.models import (
    Parameter,
    ProfileTemplate,
    ProfileTemplateItem,
)
from app.shared.pagination import PageParams
from app.shared.repository import BaseRepository

ITEM_CATEGORY_TYPE = "template_item_category"


class ProfileTemplateItemNotFoundError(Exception):
    pass


class ProfileTemplateItemReferenceError(Exception):
    """Template is missing, or category_id is not a 'template_item_category'."""


class ProfileTemplateItemService:
    """Items of a profile template, validated against template + category type."""

    def __init__(
        self,
        repository: BaseRepository[ProfileTemplateItem],
        templates: BaseRepository[ProfileTemplate],
        parameters: BaseRepository[Parameter],
    ) -> None:
        self.repository = repository
        self.templates = templates
        self.parameters = parameters

    async def list(
        self, params: PageParams, *, template_id: int | None = None
    ) -> tuple[list[ProfileTemplateItem], int]:
        filters = {"template_id": template_id} if template_id else None
        return await self.repository.list(params, filters=filters)

    async def get(self, item_id: int) -> ProfileTemplateItem:
        item = await self.repository.get(item_id)
        if item is None:
            raise ProfileTemplateItemNotFoundError(
                f"ProfileTemplateItem {item_id} not found"
            )
        return item

    async def _assert_template(self, template_id: int) -> None:
        if await self.templates.get(template_id) is None:
            raise ProfileTemplateItemReferenceError(
                f"ProfileTemplate {template_id} not found"
            )

    async def _assert_category(self, category_id: int) -> None:
        parameter = await self.parameters.get(category_id)
        if parameter is None or parameter.type != ITEM_CATEGORY_TYPE:
            raise ProfileTemplateItemReferenceError(
                f"Parameter {category_id} is not a '{ITEM_CATEGORY_TYPE}'"
            )

    async def create(
        self, data: ProfileTemplateItemCreate, actor: CurrentUser
    ) -> ProfileTemplateItem:
        await self._assert_template(data.template_id)
        await self._assert_category(data.category_id)
        item = ProfileTemplateItem(
            template_id=data.template_id,
            category_id=data.category_id,
            name=data.name,
            created_by=actor.user_id,
            ip_created=actor.ip,
        )
        return await self.repository.add(item)

    async def update(
        self, item_id: int, data: ProfileTemplateItemUpdate, actor: CurrentUser
    ) -> ProfileTemplateItem:
        item = await self.get(item_id)
        changes = data.model_dump(exclude_unset=True)
        if "category_id" in changes:
            await self._assert_category(changes["category_id"])
        changes["updated_by"] = actor.user_id
        changes["ip_updated"] = actor.ip
        return await self.repository.update(item, changes)

    async def delete(self, item_id: int) -> None:
        item = await self.get(item_id)
        await self.repository.soft_delete(item)
