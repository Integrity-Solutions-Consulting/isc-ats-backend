from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.dependencies import CurrentUserDep, SessionDep
from app.modules.ai.api.vacancy_promo_images_schemas import (
    VacancyPromoImageCreate,
    VacancyPromoImageRead,
)
from app.modules.ai.application.vacancy_promo_images_service import (
    VacancyPromoImageNotFoundError,
    VacancyPromoImageReferenceError,
    VacancyPromoImageService,
)
from app.modules.ai.infrastructure.models import VacancyPromoImage
from app.modules.auth.api.authorization import require_permission
from app.modules.recruitment.infrastructure.models import Vacancy
from app.modules.storage.infrastructure.models import File
from app.shared.pagination import Page, PageParams
from app.shared.repository import BaseRepository

router = APIRouter(prefix="/vacancy-promo-images", tags=["ai · vacancy promo images"])


def get_service(session: SessionDep) -> VacancyPromoImageService:
    return VacancyPromoImageService(
        BaseRepository(session, VacancyPromoImage),
        BaseRepository(session, Vacancy),
        BaseRepository(session, File),
    )


ServiceDep = Annotated[VacancyPromoImageService, Depends(get_service)]


@router.get(
    "",
    response_model=Page[VacancyPromoImageRead],
    dependencies=[Depends(require_permission("ai.vacancy_promo_images.read"))],
)
async def list_promo_images(
    service: ServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    vacancy_id: Annotated[int | None, Query()] = None,
) -> Page[VacancyPromoImageRead]:
    params = PageParams(page=page, size=size)
    items, total = await service.list(params, vacancy_id=vacancy_id)
    return Page.create(
        [VacancyPromoImageRead.model_validate(i) for i in items], total, params
    )


@router.get(
    "/{image_id}",
    response_model=VacancyPromoImageRead,
    dependencies=[Depends(require_permission("ai.vacancy_promo_images.read"))],
)
async def get_promo_image(image_id: int, service: ServiceDep) -> VacancyPromoImageRead:
    try:
        return VacancyPromoImageRead.model_validate(await service.get(image_id))
    except VacancyPromoImageNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post(
    "",
    response_model=VacancyPromoImageRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("ai.vacancy_promo_images.create"))],
)
async def create_promo_image(
    data: VacancyPromoImageCreate, service: ServiceDep, current_user: CurrentUserDep
) -> VacancyPromoImageRead:
    try:
        created = await service.create(data, current_user)
    except VacancyPromoImageReferenceError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return VacancyPromoImageRead.model_validate(created)


@router.delete(
    "/{image_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("ai.vacancy_promo_images.delete"))],
)
async def delete_promo_image(image_id: int, service: ServiceDep) -> None:
    try:
        await service.delete(image_id)
    except VacancyPromoImageNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
