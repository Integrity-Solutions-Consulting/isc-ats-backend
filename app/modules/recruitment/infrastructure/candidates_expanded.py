from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.modules.auth.infrastructure.models import User
from app.modules.org.infrastructure.models import Parameter
from app.modules.recruitment.api.candidates_schemas import (
    CandidateAppliedVacancy,
    CandidateDirectoryRead,
)
from app.modules.recruitment.infrastructure.application_models import Application
from app.modules.recruitment.infrastructure.candidate_models import Candidate
from app.modules.recruitment.infrastructure.models import Vacancy
from app.modules.talent.infrastructure.models import TalentPool
from app.shared.pagination import PageParams


@dataclass
class CandidateExpanded:
    id: int
    user_id: int
    email: str
    first_name: str
    last_name: str
    doc_type: str
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
    years_of_experience: Decimal | None
    cv_file_id: int | None
    avatar_file_id: int | None
    is_active: bool
    created_at: datetime
    created_by: int | None = None
    candidate_id: int | None = None
    skills: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    is_in_talent_pool: bool = False
    talent_pool_id: int | None = None
    source_vacancy_id: int | None = None
    source_vacancy_title: str | None = None
    applied_vacancies: list[CandidateAppliedVacancy] = field(default_factory=list)


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
                Candidate.doc_type,
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
                Candidate.years_of_experience,
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
            await self.session.execute(self._base_stmt().where(Candidate.id == candidate_id))
        ).one_or_none()
        if row is None:
            return None
        return CandidateExpanded(**row._asdict())

    async def list_directory(
        self,
        params: PageParams,
        *,
        search: str | None = None,
        skill: str | None = None,
        tool: str | None = None,
        career: str | None = None,
        career_id: int | None = None,
        city_id: int | None = None,
        candidate_id: int | None = None,
        vacancy_id: int | None = None,
        in_talent_pool: bool | None = None,
        only_applied: bool = False,
        include_inactive: bool = False,
    ) -> tuple[list[CandidateDirectoryRead], int]:
        """Query all candidates matching table view filters, enriched with
        parsed skills/tools, applied vacancies, and talent pool enrollment."""
        CityParam = aliased(Parameter, name="city_p")
        EducationParam = aliased(Parameter, name="education_p")
        CareerParam = aliased(Parameter, name="career_p")
        TitleParam = aliased(Parameter, name="title_p")
        UniversityParam = aliased(Parameter, name="university_p")

        # Subquery for active talent pool status (prevents row multiplication)
        tp_subq = (
            select(
                TalentPool.candidate_id,
                func.min(TalentPool.id).label("talent_pool_id"),
            )
            .where(TalentPool.is_active.is_(True))
            .group_by(TalentPool.candidate_id)
            .subquery("tp_active")
        )

        stmt = (
            select(
                Candidate.id,
                Candidate.user_id,
                User.email,
                Candidate.first_name,
                Candidate.last_name,
                Candidate.doc_type,
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
                Candidate.years_of_experience,
                Candidate.cv_file_id,
                Candidate.avatar_file_id,
                Candidate.is_active,
                Candidate.created_at,
                Candidate.created_by,
                Candidate.parsed_data,
                tp_subq.c.talent_pool_id,
            )
            .join(User, Candidate.user_id == User.id)
            .outerjoin(CityParam, Candidate.city_id == CityParam.id)
            .outerjoin(EducationParam, Candidate.education_level_id == EducationParam.id)
            .outerjoin(CareerParam, Candidate.career_id == CareerParam.id)
            .outerjoin(TitleParam, Candidate.title_id == TitleParam.id)
            .outerjoin(UniversityParam, Candidate.university_id == UniversityParam.id)
            .outerjoin(tp_subq, Candidate.id == tp_subq.c.candidate_id)
        )

        if not include_inactive:
            stmt = stmt.where(Candidate.is_active.is_(True))

        if candidate_id is not None:
            stmt = stmt.where(Candidate.id == candidate_id)

        if in_talent_pool is True:
            stmt = stmt.where(tp_subq.c.talent_pool_id.isnot(None))
        elif in_talent_pool is False:
            stmt = stmt.where(tp_subq.c.talent_pool_id.is_(None))

        if only_applied:
            has_app = (
                select(1)
                .select_from(Application)
                .where(
                    Application.candidate_id == Candidate.id,
                    Application.is_active.is_(True),
                )
            )
            stmt = stmt.where(has_app.exists())

        if vacancy_id is not None:
            has_vac = (
                select(1)
                .select_from(Application)
                .where(
                    Application.candidate_id == Candidate.id,
                    Application.vacancy_id == vacancy_id,
                    Application.is_active.is_(True),
                )
            )
            stmt = stmt.where(has_vac.exists())

        if career_id is not None:
            stmt = stmt.where(Candidate.career_id == career_id)
        elif career:
            stmt = stmt.where(CareerParam.name.ilike(f"%{career.strip()}%"))

        if city_id is not None:
            stmt = stmt.where(Candidate.city_id == city_id)

        if skill:
            for s in [x.strip() for x in skill.split(",") if x.strip()]:
                stmt = stmt.where(Candidate.parsed_data["skills"].astext.ilike(f"%{s}%"))

        if tool:
            for t in [x.strip() for x in tool.split(",") if x.strip()]:
                stmt = stmt.where(Candidate.parsed_data["tools"].astext.ilike(f"%{t}%"))

        if search:
            term = f"%{search.strip()}%"
            stmt = stmt.where(
                (Candidate.first_name.ilike(term))
                | (Candidate.last_name.ilike(term))
                | (User.email.ilike(term))
                | (Candidate.cedula.ilike(term))
            )

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await self.session.execute(count_stmt)).scalar_one()

        stmt = (
            stmt.order_by(Candidate.created_at.desc(), Candidate.id.desc())
            .offset(params.offset)
            .limit(params.limit)
        )
        rows = (await self.session.execute(stmt)).all()

        if not rows:
            return [], total

        cand_ids = [r.id for r in rows]
        VacNameParam = aliased(Parameter, name="vac_name_p")
        StatusParam = aliased(Parameter, name="status_p")

        app_stmt = (
            select(
                Application.candidate_id,
                Application.vacancy_id,
                VacNameParam.name.label("vacancy_title"),
                StatusParam.name.label("status"),
                Application.match_score,
            )
            .join(Vacancy, Application.vacancy_id == Vacancy.id)
            .outerjoin(VacNameParam, Vacancy.vacancy_name_id == VacNameParam.id)
            .outerjoin(StatusParam, Application.status_id == StatusParam.id)
            .where(
                Application.candidate_id.in_(cand_ids),
                Application.is_active.is_(True),
            )
            .order_by(Application.applied_at.desc())
        )
        app_rows = (await self.session.execute(app_stmt)).all()

        apps_by_candidate: dict[int, list[CandidateAppliedVacancy]] = {}
        for ar in app_rows:
            apps_by_candidate.setdefault(ar.candidate_id, []).append(
                CandidateAppliedVacancy(
                    id=ar.vacancy_id,
                    title=ar.vacancy_title or "Sin título",
                    status=ar.status,
                    match_score=ar.match_score,
                )
            )

        items: list[CandidateDirectoryRead] = []
        for r in rows:
            pd = r.parsed_data or {}
            skills = pd.get("skills", []) if isinstance(pd, dict) else []
            tools = pd.get("tools", []) if isinstance(pd, dict) else []

            cand_apps = apps_by_candidate.get(r.id, [])
            applied_titles = [a.title for a in cand_apps]
            first_vac_id = cand_apps[0].id if cand_apps else None
            vac_title_str = ", ".join(applied_titles) if applied_titles else None

            items.append(
                CandidateDirectoryRead(
                    id=r.id,
                    candidate_id=r.id,
                    user_id=r.user_id,
                    email=r.email,
                    first_name=r.first_name,
                    last_name=r.last_name,
                    doc_type=r.doc_type,
                    cedula=r.cedula,
                    birth_date=r.birth_date,
                    phone=r.phone,
                    city=r.city,
                    education_level=r.education_level,
                    career=r.career,
                    title=r.title,
                    university=r.university,
                    home_address=r.home_address,
                    is_studying=r.is_studying,
                    is_working=r.is_working,
                    current_company=r.current_company,
                    years_of_experience=r.years_of_experience,
                    cv_file_id=r.cv_file_id,
                    avatar_file_id=r.avatar_file_id,
                    is_active=r.is_active,
                    created_at=r.created_at,
                    created_by=r.created_by,
                    skills=skills if isinstance(skills, list) else [],
                    tools=tools if isinstance(tools, list) else [],
                    is_in_talent_pool=r.talent_pool_id is not None,
                    talent_pool_id=r.talent_pool_id,
                    source_vacancy_id=first_vac_id,
                    source_vacancy_title=vac_title_str,
                    applied_vacancies=cand_apps,
                )
            )

        return items, total