from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.recruitment.infrastructure.application_models import Application
from app.shared.repository import BaseRepository


class ApplicationRepository(BaseRepository[Application]):
    """Repository for recruitment.applications — adds the (vacancy, candidate) lookup.

    The (vacancy_id, candidate_id) pair is unique across ALL rows (the index does
    not filter is_active), so the duplicate lookup must see inactive rows too in
    order to resurrect a withdrawn application instead of violating the index.
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Application)

    async def get_by_vacancy_and_candidate(
        self, vacancy_id: int, candidate_id: int
    ) -> Application | None:
        stmt = (
            select(Application)
            .where(Application.vacancy_id == vacancy_id)
            .where(Application.candidate_id == candidate_id)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()
