from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.dependencies import CurrentUserDep, SessionDep
from app.modules.auth.api.authorization import require_permission
from app.modules.auth.api.menu_items_schemas import (
    MenuItemCreate,
    MenuItemRead,
    MenuItemUpdate,
    MenuNode,
)
from app.modules.auth.application.menu_items_service import (
    MenuItemNotFoundError,
    MenuItemReferenceError,
    MenuItemService,
)
from app.modules.auth.infrastructure.authorization_repository import (
    AuthorizationRepository,
)
from app.modules.auth.infrastructure.menu_items_repository import MenuItemRepository
from app.modules.auth.infrastructure.models import Permission
from app.modules.auth.infrastructure.repository import UserRepository
from app.modules.org.infrastructure.models import Parameter
from app.shared.pagination import Page, PageParams
from app.shared.repository import BaseRepository

router = APIRouter(prefix="/menu-items", tags=["auth · menu items"])


def get_service(session: SessionDep) -> MenuItemService:
    return MenuItemService(
        MenuItemRepository(session),
        BaseRepository(session, Parameter),
        BaseRepository(session, Permission),
    )


ServiceDep = Annotated[MenuItemService, Depends(get_service)]


@router.get("/me", response_model=list[MenuNode])
async def my_menu(
    current_user: CurrentUserDep, service: ServiceDep, session: SessionDep
) -> list[MenuNode]:
    """The authenticated user's navigation tree for their own portal.

    Items gated by a permission the user lacks (and their branches) are omitted.
    """
    user = await UserRepository(session).get(current_user.user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User no longer active")
    allowed = await AuthorizationRepository(session).list_permission_ids_for_user(
        current_user.user_id
    )
    return await service.get_menu_for_user(user.portal_id, allowed)


@router.get(
    "",
    response_model=Page[MenuItemRead],
    dependencies=[Depends(require_permission("auth.menu_items.read"))],
)
async def list_menu_items(
    service: ServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    portal_id: Annotated[int | None, Query(description="Filter by portal")] = None,
) -> Page[MenuItemRead]:
    params = PageParams(page=page, size=size)
    items, total = await service.list(params, portal_id=portal_id)
    return Page.create([MenuItemRead.model_validate(i) for i in items], total, params)


@router.get(
    "/{menu_item_id}",
    response_model=MenuItemRead,
    dependencies=[Depends(require_permission("auth.menu_items.read"))],
)
async def get_menu_item(menu_item_id: int, service: ServiceDep) -> MenuItemRead:
    try:
        return MenuItemRead.model_validate(await service.get(menu_item_id))
    except MenuItemNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post(
    "",
    response_model=MenuItemRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("auth.menu_items.create"))],
)
async def create_menu_item(
    data: MenuItemCreate, service: ServiceDep, current_user: CurrentUserDep
) -> MenuItemRead:
    try:
        created = await service.create(data, current_user)
    except MenuItemReferenceError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return MenuItemRead.model_validate(created)


@router.patch(
    "/{menu_item_id}",
    response_model=MenuItemRead,
    dependencies=[Depends(require_permission("auth.menu_items.update"))],
)
async def update_menu_item(
    menu_item_id: int,
    data: MenuItemUpdate,
    service: ServiceDep,
    current_user: CurrentUserDep,
) -> MenuItemRead:
    try:
        updated = await service.update(menu_item_id, data, current_user)
    except MenuItemNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except MenuItemReferenceError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return MenuItemRead.model_validate(updated)


@router.delete(
    "/{menu_item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("auth.menu_items.delete"))],
)
async def delete_menu_item(menu_item_id: int, service: ServiceDep) -> None:
    try:
        await service.delete(menu_item_id)
    except MenuItemNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
