from __future__ import annotations

from collections import defaultdict

from app.core.dependencies import CurrentUser
from app.modules.auth.api.menu_items_schemas import (
    MenuItemCreate,
    MenuItemUpdate,
    MenuNode,
)
from app.modules.auth.infrastructure.menu_items_repository import MenuItemRepository
from app.modules.auth.infrastructure.models import MenuItem, Permission
from app.modules.org.infrastructure.models import Parameter
from app.shared.pagination import PageParams
from app.shared.repository import BaseRepository


class MenuItemNotFoundError(Exception):
    pass


class MenuItemReferenceError(Exception):
    """A referenced portal, parent, or permission does not exist (or is inactive)."""


class MenuItemService:
    """CRUD for auth.menu_items plus the permission-filtered menu tree for a user.

    FKs (portal -> org.parameters, parent -> menu_items, permission ->
    auth.permissions) are validated up front so the API returns a clear 422.
    """

    def __init__(
        self,
        repository: MenuItemRepository,
        portals: BaseRepository[Parameter],
        permissions: BaseRepository[Permission],
    ) -> None:
        self.repository = repository
        self.portals = portals
        self.permissions = permissions

    async def list(
        self, params: PageParams, *, portal_id: int | None = None
    ) -> tuple[list[MenuItem], int]:
        filters = {"portal_id": portal_id} if portal_id else None
        return await self.repository.list(params, filters=filters)

    async def get(self, menu_item_id: int) -> MenuItem:
        item = await self.repository.get(menu_item_id)
        if item is None:
            raise MenuItemNotFoundError(f"MenuItem {menu_item_id} not found")
        return item

    async def create(self, data: MenuItemCreate, actor: CurrentUser) -> MenuItem:
        await self._validate_refs(data.portal_id, data.parent_id, data.permission_id)
        item = MenuItem(
            portal_id=data.portal_id,
            label=data.label,
            order=data.order,
            parent_id=data.parent_id,
            route=data.route,
            icon=data.icon,
            permission_id=data.permission_id,
            created_by=actor.user_id,
            ip_created=actor.ip,
        )
        return await self.repository.add(item)

    async def update(
        self, menu_item_id: int, data: MenuItemUpdate, actor: CurrentUser
    ) -> MenuItem:
        item = await self.get(menu_item_id)
        changes = data.model_dump(exclude_unset=True)
        await self._validate_refs(
            None,
            changes.get("parent_id") if "parent_id" in changes else None,
            changes.get("permission_id") if "permission_id" in changes else None,
            parent_self_id=menu_item_id,
        )
        changes["updated_by"] = actor.user_id
        changes["ip_updated"] = actor.ip
        return await self.repository.update(item, changes)

    async def delete(self, menu_item_id: int) -> None:
        item = await self.get(menu_item_id)
        await self.repository.soft_delete(item)

    async def get_menu_for_user(
        self, portal_id: int, allowed_permission_ids: set[int]
    ) -> list[MenuNode]:
        items = await self.repository.list_by_portal(portal_id)
        visible = [
            i
            for i in items
            if i.permission_id is None or i.permission_id in allowed_permission_ids
        ]
        return _build_tree(visible)

    async def _validate_refs(
        self,
        portal_id: int | None,
        parent_id: int | None,
        permission_id: int | None,
        *,
        parent_self_id: int | None = None,
    ) -> None:
        if portal_id is not None and await self.portals.get(portal_id) is None:
            raise MenuItemReferenceError(f"Portal {portal_id} not found")
        if parent_id is not None:
            if parent_id == parent_self_id:
                raise MenuItemReferenceError("A menu item cannot be its own parent")
            if await self.repository.get(parent_id) is None:
                raise MenuItemReferenceError(f"Parent menu item {parent_id} not found")
        if permission_id is not None and await self.permissions.get(permission_id) is None:
            raise MenuItemReferenceError(f"Permission {permission_id} not found")


def _build_tree(items: list[MenuItem]) -> list[MenuNode]:
    """Assemble visible items into a forest. Items orphaned by a hidden parent drop out.

    Input is assumed ordered by (order, id), so siblings keep their order.
    """
    visible_ids = {i.id for i in items}
    children: dict[int, list[MenuItem]] = defaultdict(list)
    roots: list[MenuItem] = []
    for item in items:
        if item.parent_id is None:
            roots.append(item)
        elif item.parent_id in visible_ids:
            children[item.parent_id].append(item)
        # else: parent hidden by permissions -> the whole branch is hidden

    def to_node(item: MenuItem) -> MenuNode:
        return MenuNode(
            id=item.id,
            label=item.label,
            order=item.order,
            route=item.route,
            icon=item.icon,
            children=[to_node(c) for c in children.get(item.id, [])],
        )

    return [to_node(r) for r in roots]
