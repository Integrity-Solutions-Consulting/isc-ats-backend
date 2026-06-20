"""Intra-org usage checks for delete guards.

Answers "does this org row still have active dependents within the org module?"
(processes under a department/company, contacts under a company). Vacancy-side
usage is covered separately by recruitment's VacancyUsageRepository.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.org.infrastructure.models import Contact, Process


class OrgUsageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _exists_active(self, model: type, column: str, value: int) -> bool:
        stmt = (
            select(func.count())
            .select_from(model)
            .where(getattr(model, column) == value)
            .where(model.is_active.is_(True))
        )
        return (await self.session.execute(stmt)).scalar_one() > 0

    async def has_active_processes_for_department(self, department_id: int) -> bool:
        return await self._exists_active(Process, "department_id", department_id)

    async def has_active_processes_for_company(self, company_id: int) -> bool:
        return await self._exists_active(Process, "client_company_id", company_id)

    async def has_active_contacts_for_company(self, company_id: int) -> bool:
        return await self._exists_active(Contact, "client_company_id", company_id)
