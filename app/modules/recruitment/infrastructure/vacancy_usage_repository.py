"""Cross-module usage checks against recruitment.vacancies.

Lets org-side services (process, contact, department, client, template, catalog)
answer "is this row still referenced by a live vacancy?" without importing
recruitment into their service layer — the route wires this in as a callable port.

A vacancy counts as "live" when it is active (not soft-deleted) and its status is
not 'closed' or 'cancelled'. Closed/cancelled vacancies are historical and must
not block the deletion of the catalog rows they referenced.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.org.infrastructure.models import Parameter
from app.modules.recruitment.infrastructure.models import Vacancy

# Vacancy FK columns other modules may guard on.
_ALLOWED_COLUMNS = frozenset(
    {
        "process_id",
        "contact_id",
        "department_id",
        "client_company_id",
        "profile_template_id",
        "vacancy_name_id",
        "career_id",
        "city_id",
        "work_mode_id",
        "resource_level_id",
        "status_id",
    }
)

_TERMINAL_STATUS_CODES = ("closed", "cancelled")


class VacancyUsageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def is_referenced_by_live_vacancy(self, column: str, value: int) -> bool:
        if column not in _ALLOWED_COLUMNS:
            raise ValueError(f"Unsupported vacancy column: {column}")

        terminal_status_ids = select(Parameter.id).where(
            Parameter.type == "vacancy_status",
            Parameter.code.in_(_TERMINAL_STATUS_CODES),
        )
        stmt = (
            select(func.count())
            .select_from(Vacancy)
            .where(getattr(Vacancy, column) == value)
            .where(Vacancy.is_active.is_(True))
            .where(Vacancy.status_id.notin_(terminal_status_ids))
        )
        count = (await self.session.execute(stmt)).scalar_one()
        return count > 0
