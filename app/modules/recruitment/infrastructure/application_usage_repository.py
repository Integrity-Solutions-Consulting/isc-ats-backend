"""Usage checks against recruitment.applications for delete guards.

A vacancy cannot be deleted while candidates are still applied to it, and a
process stage cannot be deleted while an application currently sits in it. Only
active applications count — withdrawn (soft-deleted) ones do not block deletion.

Wired at the composition root so org-side services (process stages) can run the
check without importing recruitment into their service layer.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.recruitment.infrastructure.application_models import Application


class ApplicationUsageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _count_active(self, column: str, value: int) -> int:
        stmt = (
            select(func.count())
            .select_from(Application)
            .where(getattr(Application, column) == value)
            .where(Application.is_active.is_(True))
        )
        return (await self.session.execute(stmt)).scalar_one()

    async def has_active_for_vacancy(self, vacancy_id: int) -> bool:
        return await self._count_active("vacancy_id", vacancy_id) > 0

    async def has_active_in_stage(self, process_stage_id: int) -> bool:
        return await self._count_active("current_stage_id", process_stage_id) > 0
