from app.core.dependencies import CurrentUser
from app.modules.recruitment.infrastructure.candidate_models import Candidate
from app.modules.recruitment.infrastructure.models import Vacancy
from app.modules.talent.api.talent_pool_schemas import TalentPoolCreate
from app.modules.talent.infrastructure.models import TalentPool
from app.shared.pagination import PageParams
from app.shared.repository import BaseRepository


class TalentPoolNotFoundError(Exception):
    pass


class TalentPoolReferenceError(Exception):
    """A referenced candidate or vacancy does not exist."""


class TalentPoolService:
    def __init__(
        self,
        repository: BaseRepository[TalentPool],
        candidates: BaseRepository[Candidate],
        vacancies: BaseRepository[Vacancy],
    ) -> None:
        self.repository = repository
        self.candidates = candidates
        self.vacancies = vacancies

    async def list(
        self,
        params: PageParams,
        *,
        candidate_id: int | None = None,
        source_vacancy_id: int | None = None,
    ) -> tuple[list[TalentPool], int]:
        filters = {
            k: v
            for k, v in {
                "candidate_id": candidate_id,
                "source_vacancy_id": source_vacancy_id,
            }.items()
            if v is not None
        }
        return await self.repository.list(params, filters=filters or None)

    async def get(self, entry_id: int) -> TalentPool:
        entry = await self.repository.get(entry_id)
        if entry is None:
            raise TalentPoolNotFoundError(f"Talent pool entry {entry_id} not found")
        return entry

    async def create(self, data: TalentPoolCreate, actor: CurrentUser) -> TalentPool:
        if await self.candidates.get(data.candidate_id) is None:
            raise TalentPoolReferenceError(
                f"candidate_id={data.candidate_id} not found"
            )
        if data.source_vacancy_id is not None:
            if await self.vacancies.get(data.source_vacancy_id) is None:
                raise TalentPoolReferenceError(
                    f"source_vacancy_id={data.source_vacancy_id} not found"
                )
        entry = TalentPool(
            **data.model_dump(),
            created_by=actor.user_id,
            ip_created=actor.ip,
        )
        return await self.repository.add(entry)

    async def delete(self, entry_id: int) -> None:
        entry = await self.get(entry_id)
        await self.repository.soft_delete(entry)
