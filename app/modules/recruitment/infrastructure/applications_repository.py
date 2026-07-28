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

    async def list_active_for_vacancy(
        self, vacancy_id: int, *, exclude_status_id: int | None = None
    ) -> list[Application]:
        """Active applications for `vacancy_id`, optionally excluding one status.

        Used by ApplicationService.auto_reject_for_vacancy to find every
        application that must be auto-rejected when the vacancy closes/deletes
        (every active one except those already 'hired').
        """
        stmt = (
            select(Application)
            .where(Application.vacancy_id == vacancy_id)
            .where(Application.is_active.is_(True))
        )
        if exclude_status_id is not None:
            stmt = stmt.where(Application.status_id != exclude_status_id)
        return list((await self.session.execute(stmt)).scalars().all())
