from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.dependencies import CurrentUserDep, SessionDep
from app.modules.auth.api.authorization import require_permission
from app.modules.org.api.profile_templates_schemas import (
    ProfileTemplateCreate,
    ProfileTemplateRead,
    ProfileTemplateUpdate,
)
from app.modules.org.application.profile_templates_service import (
    ProfileTemplateNotFoundError,
    ProfileTemplateService,
)
from app.modules.org.infrastructure.models import ProfileTemplate
from app.shared.pagination import Page, PageParams
from app.shared.repository import BaseRepository

router = APIRouter(prefix="/profile-templates", tags=["org · profile templates"])


def get_service(session: SessionDep) -> ProfileTemplateService:
    return ProfileTemplateService(BaseRepository(session, ProfileTemplate))


ServiceDep = Annotated[ProfileTemplateService, Depends(get_service)]


@router.get(
    "",
    response_model=Page[ProfileTemplateRead],
    dependencies=[Depends(require_permission("org.profile_templates.read"))],
)
async def list_profile_templates(
    service: ServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[ProfileTemplateRead]:
    params = PageParams(page=page, size=size)
    items, total = await service.list(params)
    return Page.create([ProfileTemplateRead.model_validate(i) for i in items], total, params)


@router.get(
    "/{template_id}",
    response_model=ProfileTemplateRead,
    dependencies=[Depends(require_permission("org.profile_templates.read"))],
)
async def get_profile_template(
    template_id: int, service: ServiceDep
) -> ProfileTemplateRead:
    try:
        return ProfileTemplateRead.model_validate(await service.get(template_id))
    except ProfileTemplateNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post(
    "",
    response_model=ProfileTemplateRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("org.profile_templates.create"))],
)
async def create_profile_template(
    data: ProfileTemplateCreate, service: ServiceDep, current_user: CurrentUserDep
) -> ProfileTemplateRead:
    created = await service.create(data, current_user)
    return ProfileTemplateRead.model_validate(created)


@router.patch(
    "/{template_id}",
    response_model=ProfileTemplateRead,
    dependencies=[Depends(require_permission("org.profile_templates.update"))],
)
async def update_profile_template(
    template_id: int,
    data: ProfileTemplateUpdate,
    service: ServiceDep,
    current_user: CurrentUserDep,
) -> ProfileTemplateRead:
    try:
        updated = await service.update(template_id, data, current_user)
    except ProfileTemplateNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return ProfileTemplateRead.model_validate(updated)


@router.delete(
    "/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("org.profile_templates.delete"))],
)
async def delete_profile_template(template_id: int, service: ServiceDep) -> None:
    try:
        await service.delete(template_id)
    except ProfileTemplateNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
