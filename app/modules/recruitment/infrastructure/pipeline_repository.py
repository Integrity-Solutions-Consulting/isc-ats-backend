from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.modules.auth.infrastructure.models import User
from app.modules.org.infrastructure.models import Parameter, ProcessStage
from app.modules.recruitment.infrastructure.application_models import (
    Application,
    ApplicationDocument,
)
from app.modules.recruitment.infrastructure.candidate_models import Candidate
from app.modules.storage.infrastructure.models import File


@dataclass
class StageRow:
    id: int
    name: str
    order: int
    is_final_positive: bool
    is_initial: bool


@dataclass
class CardRow:
    id: int          # application id
    candidate_id: int
    vacancy_id: int
    current_stage_id: int | None
    first_name: str
    last_name: str
    avatar_file_id: int | None
    salary_expectation: float | None
    match_score: float | None
    updated_at: str | None
    created_at: str


@dataclass
class PipelineData:
    stages: list[StageRow]
    cards: list[CardRow]
    rejected_count: int
    hired_count: int
    openings: int


@dataclass
class VacancyDocRow:
    id: int
    application_id: int
    candidate_id: int
    first_name: str
    last_name: str
    file_id: int | None
    stored_key: str | None
    original_name: str | None
    stage_name: str
    created_by: int | None
    created_at: datetime
    author_email: str | None


class PipelineRepository:
    """Read-only repository that assembles the Kanban pipeline for a vacancy."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_pipeline(self, vacancy_id: int) -> PipelineData:
        # ── Process stages (Kanban columns) ───────────────────────────────────
        # Requires vacancy → process → process_stages → parameter (stage name)
        from app.modules.org.infrastructure.models import Process
        from app.modules.recruitment.infrastructure.models import Vacancy

        StageName = aliased(Parameter, name="stage_name")

        stages_stmt = (
            select(
                ProcessStage.id,
                StageName.name.label("name"),
                ProcessStage.order,
                ProcessStage.is_final_positive,
                ProcessStage.is_initial,
            )
            .join(Process, ProcessStage.process_id == Process.id)
            .join(Vacancy, Vacancy.process_id == Process.id)
            .join(StageName, ProcessStage.stage_id == StageName.id)
            .where(Vacancy.id == vacancy_id)
            .where(ProcessStage.is_active.is_(True))
            .order_by(ProcessStage.order)
        )
        stage_rows = (await self.session.execute(stages_stmt)).all()
        stages = [StageRow(**row._asdict()) for row in stage_rows]

        # ── Application cards ──────────────────────────────────────────────────
        cards_stmt = (
            select(
                Application.id,
                Application.candidate_id,
                Application.vacancy_id,
                Application.current_stage_id,
                Candidate.first_name,
                Candidate.last_name,
                Candidate.avatar_file_id,
                Application.salary_expectation,
                Application.match_score,
                Application.updated_at,
                Application.created_at,
            )
            .join(Candidate, Application.candidate_id == Candidate.id)
            .where(Application.vacancy_id == vacancy_id)
            .where(Application.is_active.is_(True))
            .order_by(Application.applied_at)
        )
        card_rows = (await self.session.execute(cards_stmt)).all()
        cards = [CardRow(**row._asdict()) for row in card_rows]

        # Count rejected (applications with no current_stage)
        rejected_count = sum(1 for c in cards if c.current_stage_id is None)

        # Build a set of stage IDs where is_final_positive=True for this vacancy
        final_positive_ids = {s.id for s in stages if s.is_final_positive}
        hired_count = sum(1 for c in cards if c.current_stage_id in final_positive_ids)

        # Resolve openings from the vacancy row
        vacancy_row = await self.session.get(Vacancy, vacancy_id)
        openings = vacancy_row.openings if vacancy_row is not None else 0

        return PipelineData(
            stages=stages,
            cards=cards,
            rejected_count=rejected_count,
            hired_count=hired_count,
            openings=openings,
        )

    async def get_vacancy_documents(self, vacancy_id: int) -> list[VacancyDocRow]:
        """Return all generated Word profile documents for a vacancy's applications.

        Joins: application_documents → applications → candidates → storage.files
               + auth.users (for author email) + process_stages → parameters (stage name)
        Only returns documents where entity_type = 'application_word' in storage.files,
        or all application_documents records with an associated file.
        """

        StageName = aliased(Parameter, name="doc_stage_name")

        stmt = (
            select(
                ApplicationDocument.id,
                ApplicationDocument.application_id,
                Application.candidate_id,
                Candidate.first_name,
                Candidate.last_name,
                ApplicationDocument.file_id,
                File.stored_key,
                File.original_name,
                func.coalesce(StageName.name, "—").label("stage_name"),
                ApplicationDocument.created_by,
                ApplicationDocument.created_at,
                User.email.label("author_email"),
            )
            .join(Application, ApplicationDocument.application_id == Application.id)
            .join(Candidate, Application.candidate_id == Candidate.id)
            .outerjoin(File, ApplicationDocument.file_id == File.id)
            .outerjoin(
                ProcessStage,
                Application.current_stage_id == ProcessStage.id,
            )
            .outerjoin(StageName, ProcessStage.stage_id == StageName.id)
            .outerjoin(User, ApplicationDocument.created_by == User.id)
            .where(Application.vacancy_id == vacancy_id)
            .where(ApplicationDocument.is_active.is_(True))
            .where(Application.is_active.is_(True))
            .order_by(ApplicationDocument.created_at.desc())
        )

        rows = (await self.session.execute(stmt)).all()
        return [VacancyDocRow(**row._asdict()) for row in rows]
