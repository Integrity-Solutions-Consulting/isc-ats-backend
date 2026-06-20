"""Catalog usage guard: is an org.parameters row still referenced anywhere?

Almost every module points a lookup FK at org.parameters (status_id, city_id,
portal_id, stage_id, ...). Before a catalog entry is soft-deleted we confirm no
active row still depends on it — otherwise reports, historical vacancies and
pipelines would render dangling references.

A parameter id is globally unique, so an id-based scan across every referencing
column is both exhaustive and type-safe: an id only ever appears in columns of
its own catalog type. Only active (is_active) rows count — soft-deleted
dependents are already gone.

This object is wired at the parameters route (composition root), so the service
layer never imports another module's infrastructure to run the check.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ai.infrastructure.models import CvParseJob
from app.modules.auth.infrastructure.models import MenuItem, User
from app.modules.comms.infrastructure.models import EmailLog, Notification
from app.modules.org.infrastructure.models import ProcessStage, ProfileTemplateItem
from app.modules.recruitment.infrastructure.application_models import (
    Application,
    ApplicationDocument,
)
from app.modules.recruitment.infrastructure.candidate_models import Candidate
from app.modules.recruitment.infrastructure.interview_models import Interview
from app.modules.recruitment.infrastructure.models import Vacancy

# Every (model, column) that holds a FK into org.parameters. Keep in sync with
# the schema — a missing pair would let an in-use parameter be deleted silently.
_REFERENCES: tuple[tuple[type, str], ...] = (
    (Vacancy, "vacancy_name_id"),
    (Vacancy, "career_id"),
    (Vacancy, "city_id"),
    (Vacancy, "work_mode_id"),
    (Vacancy, "resource_level_id"),
    (Vacancy, "status_id"),
    (Candidate, "city_id"),
    (Candidate, "education_level_id"),
    (Candidate, "career_id"),
    (Candidate, "title_id"),
    (Candidate, "university_id"),
    (Application, "status_id"),
    (Application, "current_status_id"),
    (ApplicationDocument, "status_id"),
    (Interview, "status_id"),
    (Interview, "scheduled_by_id"),
    (CvParseJob, "status_id"),
    (Notification, "channel_id"),
    (EmailLog, "status_id"),
    (ProcessStage, "stage_id"),
    (ProfileTemplateItem, "category_id"),
    (User, "portal_id"),
    (MenuItem, "portal_id"),
)


class ParameterUsageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def is_referenced(self, parameter_id: int) -> bool:
        for model, column in _REFERENCES:
            stmt = (
                select(func.count())
                .select_from(model)
                .where(getattr(model, column) == parameter_id)
                .where(model.is_active.is_(True))
            )
            if (await self.session.execute(stmt)).scalar_one() > 0:
                return True
        return False
