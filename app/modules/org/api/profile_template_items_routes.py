from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.dependencies import CurrentUserDep, SessionDep
from app.modules.auth.api.authorization import require_permission
from app.modules.org.api.profile_template_items_schemas import (
    ProfileTemplateItemCreate,
    ProfileTemplateItemRead,
    ProfileTemplateItemUpdate,
)
from app.modules.org.application.profile_template_items_service import (
    ProfileTemplateItemNotFoundError,
    ProfileTemplateItemReferenceError,
    ProfileTemplateItemService,
)
from app.modules.org.infrastructure.models import (
    Parameter,
    ProfileTemplate,
    ProfileTemplateItem,
)
from app.shared.pagination import Page, PageParams
from app.shared.repository import BaseRepository

router = APIRouter(prefix="/profile-template-items", tags=["org · profile template items"])


def get_service(session: SessionDep) -> ProfileTemplateItemService:
    return ProfileTemplateItemService(
        BaseRepository(session, ProfileTemplateItem),
        BaseRepository(session, ProfileTemplate),
        BaseRepository(session, Parameter),
    )


ServiceDep = Annotated[ProfileTemplateItemService, Depends(get_service)]


@router.get(
    "",
    response_model=Page[ProfileTemplateItemRead],
    dependencies=[Depends(require_permission("org.profile_template_items.read"))],
)
async def list_items(
    service: ServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=1000)] = 20,
    template_id: Annotated[int | None, Query()] = None,
) -> Page[ProfileTemplateItemRead]:
    params = PageParams(page=page, size=size)
    items, total = await service.list(params, template_id=template_id)
    return Page.create(
        [ProfileTemplateItemRead.model_validate(i) for i in items], total, params
    )


@router.get(
    "/{item_id}",
    response_model=ProfileTemplateItemRead,
    dependencies=[Depends(require_permission("org.profile_template_items.read"))],
)
async def get_item(item_id: int, service: ServiceDep) -> ProfileTemplateItemRead:
    try:
        return ProfileTemplateItemRead.model_validate(await service.get(item_id))
    except ProfileTemplateItemNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post(
    "",
    response_model=ProfileTemplateItemRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("org.profile_template_items.create"))],
)
async def create_item(
    data: ProfileTemplateItemCreate, service: ServiceDep, current_user: CurrentUserDep
) -> ProfileTemplateItemRead:
    try:
        created = await service.create(data, current_user)
    except ProfileTemplateItemReferenceError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return ProfileTemplateItemRead.model_validate(created)


@router.patch(
    "/{item_id}",
    response_model=ProfileTemplateItemRead,
    dependencies=[Depends(require_permission("org.profile_template_items.update"))],
)
async def update_item(
    item_id: int,
    data: ProfileTemplateItemUpdate,
    service: ServiceDep,
    current_user: CurrentUserDep,
) -> ProfileTemplateItemRead:
    try:
        updated = await service.update(item_id, data, current_user)
    except ProfileTemplateItemNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ProfileTemplateItemReferenceError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return ProfileTemplateItemRead.model_validate(updated)


@router.delete(
    "/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("org.profile_template_items.delete"))],
)
async def delete_item(item_id: int, service: ServiceDep) -> None:
    try:
        await service.delete(item_id)
    except ProfileTemplateItemNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
