from app.core.dependencies import CurrentUser
from app.modules.ai.api.vacancy_promo_images_schemas import VacancyPromoImageCreate
from app.modules.ai.infrastructure.models import VacancyPromoImage
from app.modules.recruitment.infrastructure.models import Vacancy
from app.modules.storage.infrastructure.models import File
from app.shared.pagination import PageParams
from app.shared.repository import BaseRepository


class VacancyPromoImageNotFoundError(Exception):
    pass


class VacancyPromoImageReferenceError(Exception):
    pass


class VacancyPromoImageService:
    def __init__(
        self,
        repository: BaseRepository[VacancyPromoImage],
        vacancies: BaseRepository[Vacancy],
        files: BaseRepository[File],
    ) -> None:
        self.repository = repository
        self.vacancies = vacancies
        self.files = files

    async def list(
        self,
        params: PageParams,
        *,
        vacancy_id: int | None = None,
    ) -> tuple[list[VacancyPromoImage], int]:
        filters = {"vacancy_id": vacancy_id} if vacancy_id is not None else None
        return await self.repository.list(params, filters=filters)

    async def get(self, image_id: int) -> VacancyPromoImage:
        img = await self.repository.get(image_id)
        if img is None:
            raise VacancyPromoImageNotFoundError(f"Promo image {image_id} not found")
        return img

    async def create(
        self, data: VacancyPromoImageCreate, actor: CurrentUser
    ) -> VacancyPromoImage:
        if await self.vacancies.get(data.vacancy_id) is None:
            raise VacancyPromoImageReferenceError(
                f"vacancy_id={data.vacancy_id} not found"
            )
        if await self.files.get(data.file_id) is None:
            raise VacancyPromoImageReferenceError(f"file_id={data.file_id} not found")
        img = VacancyPromoImage(
            **data.model_dump(),
            created_by=actor.user_id,
            ip_created=actor.ip,
        )
        return await self.repository.add(img)

    async def delete(self, image_id: int) -> None:
        img = await self.get(image_id)
        await self.repository.soft_delete(img)
