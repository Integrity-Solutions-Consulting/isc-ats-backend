"""Usage checks against recruitment.applications for delete guards.

A process stage cannot be deleted while an application currently sits in it —
any active application in that stage blocks it. A vacancy, however, only blocks
deletion on an actual HIRE: every other active application (in progress or
otherwise) is auto-rejected as part of the same delete instead of blocking it
(see ApplicationService.auto_reject_for_vacancy / VacancyService.delete). Only
active applications count — withdrawn (soft-deleted) ones never block anything.

Wired at the composition root so org-side services (process stages) can run the
check without importing recruitment into their service layer.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.org.infrastructure.parameters_repository import ParameterRepository
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
        """True only if `vacancy_id` has an active application already 'hired'.

        Deleting a vacancy with a hire on record would erase that outcome from
        the audit trail, so it stays blocked (cancel the vacancy instead). Every
        other active application no longer blocks deletion — VacancyService
        auto-rejects them as part of the delete, so there is nothing left to
        dangle behind the now-inactive vacancy.
        """
        hired = await ParameterRepository(self.session).get_by_type_and_code(
            "application_status", "hired"
        )
        if hired is None:
            return False
        stmt = (
            select(func.count())
            .select_from(Application)
            .where(Application.vacancy_id == vacancy_id)
            .where(Application.is_active.is_(True))
            .where(Application.status_id == hired.id)
        )
        return (await self.session.execute(stmt)).scalar_one() > 0

    async def has_active_in_stage(self, process_stage_id: int) -> bool:
        return await self._count_active("current_stage_id", process_stage_id) > 0
