from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.modules.auth.infrastructure.models import User
from app.modules.org.infrastructure.models import Parameter
from app.modules.recruitment.infrastructure.candidate_models import Candidate
from app.shared.pagination import PageParams


@dataclass
class CandidateExpanded:
    id: int
    user_id: int
    email: str
    first_name: str
    last_name: str
    cedula: str | None
    birth_date: date | None
    phone: str | None
    city: str | None
    education_level: str | None
    career: str | None
    title: str | None
    university: str | None
    home_address: str | None
    is_studying: bool
    is_working: bool
    current_company: str | None
    cv_file_id: int | None
    avatar_file_id: int | None
    is_active: bool
    created_at: datetime


class CandidatesExpandedRepository:
    """Read-only repository for the expanded candidate view (resolved FKs)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _base_stmt(self):
        CityParam = aliased(Parameter, name="city_p")
        EducationParam = aliased(Parameter, name="education_p")
        CareerParam = aliased(Parameter, name="career_p")
        TitleParam = aliased(Parameter, name="title_p")
        UniversityParam = aliased(Parameter, name="university_p")

        return (
            select(
                Candidate.id,
                Candidate.user_id,
                User.email,
                Candidate.first_name,
                Candidate.last_name,
                Candidate.cedula,
                Candidate.birth_date,
                Candidate.phone,
                CityParam.name.label("city"),
                EducationParam.name.label("education_level"),
                CareerParam.name.label("career"),
                TitleParam.name.label("title"),
                UniversityParam.name.label("university"),
                Candidate.home_address,
                Candidate.is_studying,
                Candidate.is_working,
                Candidate.current_company,
                Candidate.cv_file_id,
                Candidate.avatar_file_id,
                Candidate.is_active,
                Candidate.created_at,
            )
            .join(User, Candidate.user_id == User.id)
            .outerjoin(CityParam, Candidate.city_id == CityParam.id)
            .outerjoin(EducationParam, Candidate.education_level_id == EducationParam.id)
            .outerjoin(CareerParam, Candidate.career_id == CareerParam.id)
            .outerjoin(TitleParam, Candidate.title_id == TitleParam.id)
            .outerjoin(UniversityParam, Candidate.university_id == UniversityParam.id)
            .where(Candidate.is_active.is_(True))
        )

    async def list_expanded(
        self, params: PageParams, *, user_id: int | None = None
    ) -> tuple[list[CandidateExpanded], int]:
        stmt = self._base_stmt()
        if user_id is not None:
            stmt = stmt.where(Candidate.user_id == user_id)

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await self.session.execute(count_stmt)).scalar_one()

        rows = (
            await self.session.execute(
                stmt.order_by(Candidate.id).offset(params.offset).limit(params.limit)
            )
        ).all()
        return [CandidateExpanded(**row._asdict()) for row in rows], total

    async def get_expanded(self, candidate_id: int) -> CandidateExpanded | None:
        row = (
            await self.session.execute(
                self._base_stmt().where(Candidate.id == candidate_id)
            )
        ).one_or_none()
        if row is None:
            return None
        return CandidateExpanded(**row._asdict())
