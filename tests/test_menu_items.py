import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import CurrentUser
from app.modules.auth.api.menu_items_schemas import MenuItemCreate
from app.modules.auth.application.menu_items_service import (
    MenuItemReferenceError,
    MenuItemService,
)
from app.modules.auth.infrastructure.menu_items_repository import MenuItemRepository
from app.modules.auth.infrastructure.models import Permission
from app.modules.auth.infrastructure.permissions_repository import PermissionRepository
from app.modules.org.infrastructure.models import Parameter
from app.modules.org.infrastructure.parameters_repository import ParameterRepository
from app.shared.repository import BaseRepository

ACTOR = CurrentUser(user_id=1, ip="127.0.0.1")


def _service(session: AsyncSession) -> MenuItemService:
    return MenuItemService(
        MenuItemRepository(session),
        BaseRepository(session, Parameter),
        BaseRepository(session, Permission),
    )


async def _staff_portal_id(session: AsyncSession) -> int:
    portal = await ParameterRepository(session).get_by_type_and_code("user_portal", "staff")
    assert portal is not None
    return portal.id


async def _make_permission(session: AsyncSession) -> Permission:
    return await PermissionRepository(session).add(
        Permission(code=f"test.{uuid.uuid4().hex[:12]}", name="Gate")
    )


async def test_create_rejects_unknown_portal(session: AsyncSession) -> None:
    with pytest.raises(MenuItemReferenceError):
        await _service(session).create(
            MenuItemCreate(portal_id=999999, label="Bad", order=1), ACTOR
        )


async def test_create_rejects_unknown_parent(session: AsyncSession) -> None:
    portal_id = await _staff_portal_id(session)
    with pytest.raises(MenuItemReferenceError):
        await _service(session).create(
            MenuItemCreate(portal_id=portal_id, label="Orphan", order=1, parent_id=999999),
            ACTOR,
        )


async def test_menu_tree_filters_by_permission(session: AsyncSession) -> None:
    service = _service(session)
    portal_id = await _staff_portal_id(session)
    gate = await _make_permission(session)

    root = await service.create(
        MenuItemCreate(portal_id=portal_id, label="Recruitment", order=1), ACTOR
    )
    gated_child = await service.create(
        MenuItemCreate(
            portal_id=portal_id,
            label="Vacancies",
            order=1,
            parent_id=root.id,
            permission_id=gate.id,
        ),
        ACTOR,
    )

    # With the gate permission: the child shows up.
    with_perm = await service.get_menu_for_user(portal_id, {gate.id})
    root_node = next(n for n in with_perm if n.id == root.id)
    assert [c.id for c in root_node.children] == [gated_child.id]

    # Without it: the child (whole branch) is hidden.
    without_perm = await service.get_menu_for_user(portal_id, set())
    root_node = next(n for n in without_perm if n.id == root.id)
    assert root_node.children == []


async def test_menu_tree_orders_siblings_by_order(session: AsyncSession) -> None:
    service = _service(session)
    portal_id = await _staff_portal_id(session)

    second = await service.create(
        MenuItemCreate(portal_id=portal_id, label="Second", order=20), ACTOR
    )
    first = await service.create(
        MenuItemCreate(portal_id=portal_id, label="First", order=10), ACTOR
    )

    tree = await service.get_menu_for_user(portal_id, set())
    ids = [n.id for n in tree]
    assert ids.index(first.id) < ids.index(second.id)
