from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import aliased

from app.core.dependencies import CurrentUser
from app.modules.auth.infrastructure.models import User
from app.modules.org.infrastructure.models import Parameter
from app.modules.recruitment.infrastructure.candidate_models import Candidate
from app.modules.recruitment.infrastructure.models import Vacancy
from app.modules.talent.api.talent_pool_schemas import TalentPoolCreate, TalentPoolRead
from app.modules.talent.infrastructure.models import TalentPool
from app.shared.pagination import PageParams
from app.shared.repository import BaseRepository


class TalentPoolNotFoundError(Exception):
    pass


class TalentPoolReferenceError(Exception):
    """A referenced candidate or vacancy does not exist."""


class DuplicateTalentPoolError(Exception):
    """The candidate is already in the talent pool for this source vacancy."""


class TalentPoolService:
    def __init__(
        self,
        repository: BaseRepository[TalentPool],
        candidates: BaseRepository[Candidate],
        vacancies: BaseRepository[Vacancy],
    ) -> None:
        self.repository = repository
        self.candidates = candidates
        self.vacancies = vacancies

    def _enriched_select(self):
        CityP = aliased(Parameter, name="city_p")
        CareerP = aliased(Parameter, name="career_p")
        TitleP = aliased(Parameter, name="title_p")
        UniP = aliased(Parameter, name="uni_p")
        VacNameP = aliased(Parameter, name="vac_name_p")

        return (
            select(
                TalentPool.id,
                TalentPool.candidate_id,
                TalentPool.source_vacancy_id,
                TalentPool.is_active,
                TalentPool.created_at,
                TalentPool.created_by,
                Candidate.first_name,
                Candidate.last_name,
                User.email,
                Candidate.phone,
                CityP.name.label("city"),
                CareerP.name.label("career"),
                TitleP.name.label("title"),
                UniP.name.label("university"),
                Candidate.years_of_experience,
                Candidate.current_company,
                Candidate.cv_file_id,
                Candidate.avatar_file_id,
                Candidate.parsed_data,
                VacNameP.name.label("source_vacancy_title"),
            )
            .join(Candidate, TalentPool.candidate_id == Candidate.id)
            .outerjoin(User, Candidate.user_id == User.id)
            .outerjoin(CityP, Candidate.city_id == CityP.id)
            .outerjoin(CareerP, Candidate.career_id == CareerP.id)
            .outerjoin(TitleP, Candidate.title_id == TitleP.id)
            .outerjoin(UniP, Candidate.university_id == UniP.id)
            .outerjoin(Vacancy, TalentPool.source_vacancy_id == Vacancy.id)
            .outerjoin(VacNameP, Vacancy.vacancy_name_id == VacNameP.id)
        )

    def _row_to_read(self, row: Any) -> TalentPoolRead:
        pd = row.parsed_data or {}
        skills = pd.get("skills", []) if isinstance(pd, dict) else []
        tools = pd.get("tools", []) if isinstance(pd, dict) else []

        return TalentPoolRead(
            id=row.id,
            candidate_id=row.candidate_id,
            source_vacancy_id=row.source_vacancy_id,
            source_vacancy_title=row.source_vacancy_title,
            is_active=row.is_active,
            created_at=row.created_at,
            created_by=row.created_by,
            first_name=row.first_name,
            last_name=row.last_name,
            email=row.email,
            phone=row.phone,
            city=row.city,
            career=row.career,
            title=row.title,
            university=row.university,
            years_of_experience=row.years_of_experience,
            current_company=row.current_company,
            cv_file_id=row.cv_file_id,
            avatar_file_id=row.avatar_file_id,
            skills=skills if isinstance(skills, list) else [],
            tools=tools if isinstance(tools, list) else [],
        )

    async def list(
        self,
        params: PageParams,
        *,
        candidate_id: int | None = None,
        source_vacancy_id: int | None = None,
        skill: str | None = None,
        tool: str | None = None,
        search: str | None = None,
        include_inactive: bool = False,
    ) -> tuple[list[TalentPoolRead], int]:
        stmt = self._enriched_select()

        if not include_inactive:
            stmt = stmt.where(TalentPool.is_active.is_(True))

        if candidate_id is not None:
            stmt = stmt.where(TalentPool.candidate_id == candidate_id)

        if source_vacancy_id is not None:
            stmt = stmt.where(TalentPool.source_vacancy_id == source_vacancy_id)

        # Filtrado por conocimientos / tecnologías (skills)
        if skill:
            for s in [x.strip() for x in skill.split(",") if x.strip()]:
                stmt = stmt.where(Candidate.parsed_data["skills"].astext.ilike(f"%{s}%"))

        # Filtrado por herramientas / software (tools)
        if tool:
            for t in [x.strip() for x in tool.split(",") if x.strip()]:
                stmt = stmt.where(Candidate.parsed_data["tools"].astext.ilike(f"%{t}%"))

        # Búsqueda general por nombre, apellido o correo
        if search:
            term = f"%{search.strip()}%"
            stmt = stmt.where(
                (Candidate.first_name.ilike(term))
                | (Candidate.last_name.ilike(term))
                | (User.email.ilike(term))
            )

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await self.repository.session.execute(count_stmt)).scalar_one()

        stmt = (
            stmt.order_by(TalentPool.created_at.desc(), TalentPool.id.desc())
            .offset(params.offset)
            .limit(params.limit)
        )
        result = await self.repository.session.execute(stmt)
        rows = result.all()

        return [self._row_to_read(r) for r in rows], total

    async def get(self, entry_id: int) -> TalentPool:
        entry = await self.repository.get(entry_id)
        if entry is None:
            raise TalentPoolNotFoundError(f"Talent pool entry {entry_id} not found")
        return entry

    async def get_enriched(self, entry_id: int) -> TalentPoolRead:
        stmt = self._enriched_select().where(
            TalentPool.id == entry_id, TalentPool.is_active.is_(True)
        )
        row = (await self.repository.session.execute(stmt)).one_or_none()
        if row is None:
            raise TalentPoolNotFoundError(f"Talent pool entry {entry_id} not found")
        return self._row_to_read(row)

    async def create(self, data: TalentPoolCreate, actor: CurrentUser) -> TalentPool:
        if await self.candidates.get(data.candidate_id) is None:
            raise TalentPoolReferenceError(
                f"candidate_id={data.candidate_id} not found"
            )
        if data.source_vacancy_id is not None:
            if await self.vacancies.get(data.source_vacancy_id) is None:
                raise TalentPoolReferenceError(
                    f"source_vacancy_id={data.source_vacancy_id} not found"
                )
        # A candidate may be saved multiple times, but only once per source
        # vacancy (source_vacancy_id None == the general pool). Reject repeats so
        # re-adding the same pair never silently duplicates the entry.
        _, existing = await self.repository.list(
            PageParams(page=1, size=1),
            filters={
                "candidate_id": data.candidate_id,
                "source_vacancy_id": data.source_vacancy_id,
            },
        )
        if existing > 0:
            raise DuplicateTalentPoolError(
                "El candidato ya está en el banco de talento para esta vacante."
            )
        entry = TalentPool(
            **data.model_dump(),
            created_by=actor.user_id,
            ip_created=actor.ip,
        )
        return await self.repository.add(entry)

    async def delete(self, entry_id: int) -> None:
        entry = await self.get(entry_id)
        await self.repository.soft_delete(entry)