from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.modules.org.infrastructure.models import (
    ClientCompany,
    Contact,
    Department,
    Parameter,
    Process,
)
from app.modules.recruitment.infrastructure.models import Vacancy
from app.shared.pagination import PageParams


@dataclass
class VacancyExpanded:
    id: int
    vacancy_name: str
    client_company: str
    contact_id: int
    contact: str
    department: str
    process: str
    career: str
    city: str
    work_mode: str
    resource_level: str
    vacancy_status: str
    openings: int
    experience_years: int
    work_schedule: str | None
    project_duration_years: int
    project_duration_months: int
    description: str | None
    profile_requirements: dict[str, Any] | None
    profile_template_id: int | None
    is_active: bool
    created_at: datetime


class VacanciesExpandedRepository:
    """Read-only repository for the expanded vacancy list view.

    Resolves all FK fields to their human-readable labels in a single SQL
    query, avoiding N+1 lookups on the API layer.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_expanded(
        self,
        params: PageParams,
        *,
        client_company_id: int | None = None,
        status_id: int | None = None,
        department_id: int | None = None,
        include_inactive: bool = False,
    ) -> tuple[list[VacancyExpanded], int]:
        VacancyName = aliased(Parameter, name="vn")
        CareerParam = aliased(Parameter, name="cp")
        CityParam = aliased(Parameter, name="city_p")
        WorkModeParam = aliased(Parameter, name="wm_p")
        LevelParam = aliased(Parameter, name="rl_p")
        StatusParam = aliased(Parameter, name="vs_p")

        stmt = (
            select(
                Vacancy.id,
                VacancyName.name.label("vacancy_name"),
                ClientCompany.name.label("client_company"),
                Vacancy.contact_id,
                func.concat(Contact.first_name, " ", Contact.last_name).label("contact"),
                Department.name.label("department"),
                Process.name.label("process"),
                CareerParam.name.label("career"),
                CityParam.name.label("city"),
                WorkModeParam.code.label("work_mode"),
                LevelParam.code.label("resource_level"),
                StatusParam.code.label("vacancy_status"),
                Vacancy.openings,
                Vacancy.experience_years,
                Vacancy.work_schedule,
                Vacancy.project_duration_years,
                Vacancy.project_duration_months,
                Vacancy.description,
                Vacancy.profile_requirements,
                Vacancy.profile_template_id,
                Vacancy.is_active,
                Vacancy.created_at,
            )
            .join(VacancyName, Vacancy.vacancy_name_id == VacancyName.id)
            .join(ClientCompany, Vacancy.client_company_id == ClientCompany.id)
            .join(Contact, Vacancy.contact_id == Contact.id)
            .join(Department, Vacancy.department_id == Department.id)
            .join(Process, Vacancy.process_id == Process.id)
            .join(CareerParam, Vacancy.career_id == CareerParam.id)
            .join(CityParam, Vacancy.city_id == CityParam.id)
            .join(WorkModeParam, Vacancy.work_mode_id == WorkModeParam.id)
            .join(LevelParam, Vacancy.resource_level_id == LevelParam.id)
            .join(StatusParam, Vacancy.status_id == StatusParam.id)
        )

        if not include_inactive:
            stmt = stmt.where(Vacancy.is_active.is_(True))
        if client_company_id is not None:
            stmt = stmt.where(Vacancy.client_company_id == client_company_id)
        if status_id is not None:
            stmt = stmt.where(Vacancy.status_id == status_id)
        if department_id is not None:
            stmt = stmt.where(Vacancy.department_id == department_id)

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await self.session.execute(count_stmt)).scalar_one()

        rows = (
            await self.session.execute(
                stmt.order_by(Vacancy.id.desc()).offset(params.offset).limit(params.limit)
            )
        ).all()

        return [VacancyExpanded(**row._asdict()) for row in rows], total

    async def get_expanded(self, vacancy_id: int) -> VacancyExpanded | None:
        """Return a single vacancy with all FK fields resolved, or None if not found."""
        VacancyName = aliased(Parameter, name="vn")
        CareerParam = aliased(Parameter, name="cp")
        CityParam = aliased(Parameter, name="city_p")
        WorkModeParam = aliased(Parameter, name="wm_p")
        LevelParam = aliased(Parameter, name="rl_p")
        StatusParam = aliased(Parameter, name="vs_p")

        stmt = (
            select(
                Vacancy.id,
                VacancyName.name.label("vacancy_name"),
                ClientCompany.name.label("client_company"),
                Vacancy.contact_id,
                func.concat(Contact.first_name, " ", Contact.last_name).label("contact"),
                Department.name.label("department"),
                Process.name.label("process"),
                CareerParam.name.label("career"),
                CityParam.name.label("city"),
                WorkModeParam.code.label("work_mode"),
                LevelParam.code.label("resource_level"),
                StatusParam.code.label("vacancy_status"),
                Vacancy.openings,
                Vacancy.experience_years,
                Vacancy.work_schedule,
                Vacancy.project_duration_years,
                Vacancy.project_duration_months,
                Vacancy.description,
                Vacancy.profile_requirements,
                Vacancy.profile_template_id,
                Vacancy.is_active,
                Vacancy.created_at,
            )
            .join(VacancyName, Vacancy.vacancy_name_id == VacancyName.id)
            .join(ClientCompany, Vacancy.client_company_id == ClientCompany.id)
            .join(Contact, Vacancy.contact_id == Contact.id)
            .join(Department, Vacancy.department_id == Department.id)
            .join(Process, Vacancy.process_id == Process.id)
            .join(CareerParam, Vacancy.career_id == CareerParam.id)
            .join(CityParam, Vacancy.city_id == CityParam.id)
            .join(WorkModeParam, Vacancy.work_mode_id == WorkModeParam.id)
            .join(LevelParam, Vacancy.resource_level_id == LevelParam.id)
            .join(StatusParam, Vacancy.status_id == StatusParam.id)
            .where(Vacancy.id == vacancy_id)
            .where(Vacancy.is_active.is_(True))
        )
        row = (await self.session.execute(stmt)).one_or_none()
        return VacancyExpanded(**row._asdict()) if row else None
