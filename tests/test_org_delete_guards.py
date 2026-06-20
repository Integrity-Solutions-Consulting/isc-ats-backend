"""Referential delete guards for org catalog entities.

An entity cannot be soft-deleted while a live (non-closed) vacancy references it.
Department/Client additionally consider their active org dependents (processes,
and for Client also contacts).
"""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.org.application.client_companies_service import (
    ClientCompanyInUseError,
    ClientCompanyService,
)
from app.modules.org.application.contacts_service import (
    ContactInUseError,
    ContactService,
)
from app.modules.org.application.departments_service import (
    DepartmentInUseError,
    DepartmentService,
)
from app.modules.org.application.profile_templates_service import (
    ProfileTemplateInUseError,
    ProfileTemplateService,
)
from app.modules.org.infrastructure.models import (
    ClientCompany,
    Contact,
    Department,
    Parameter,
    Process,
    ProfileTemplate,
)
from app.modules.org.infrastructure.org_usage_repository import OrgUsageRepository
from app.modules.org.infrastructure.parameters_repository import ParameterRepository
from app.modules.recruitment.infrastructure.models import Vacancy
from app.modules.recruitment.infrastructure.vacancy_usage_repository import (
    VacancyUsageRepository,
)
from app.shared.repository import BaseRepository

# ── Service factories (mirror the route composition roots) ──────────────────


def _contact_service(session: AsyncSession) -> ContactService:
    usage = VacancyUsageRepository(session)
    return ContactService(
        BaseRepository(session, Contact),
        BaseRepository(session, ClientCompany),
        in_use_checker=lambda cid: usage.is_referenced_by_live_vacancy("contact_id", cid),
    )


def _department_service(session: AsyncSession) -> DepartmentService:
    usage = VacancyUsageRepository(session)
    org = OrgUsageRepository(session)

    async def checker(did: int) -> bool:
        return await usage.is_referenced_by_live_vacancy(
            "department_id", did
        ) or await org.has_active_processes_for_department(did)

    return DepartmentService(BaseRepository(session, Department), in_use_checker=checker)


def _client_service(session: AsyncSession) -> ClientCompanyService:
    usage = VacancyUsageRepository(session)
    org = OrgUsageRepository(session)

    async def checker(cid: int) -> bool:
        return (
            await usage.is_referenced_by_live_vacancy("client_company_id", cid)
            or await org.has_active_contacts_for_company(cid)
            or await org.has_active_processes_for_company(cid)
        )

    return ClientCompanyService(BaseRepository(session, ClientCompany), in_use_checker=checker)


def _template_service(session: AsyncSession) -> ProfileTemplateService:
    usage = VacancyUsageRepository(session)
    return ProfileTemplateService(
        BaseRepository(session, ProfileTemplate),
        in_use_checker=lambda tid: usage.is_referenced_by_live_vacancy(
            "profile_template_id", tid
        ),
    )


# ── Graph builder ───────────────────────────────────────────────────────────


async def _status(session: AsyncSession, code: str) -> Parameter:
    p = await ParameterRepository(session).get_by_type_and_code("vacancy_status", code)
    assert p is not None
    return p


async def _graph(session: AsyncSession):
    """Create a company + contact + department + process + a profile template."""
    sp = await BaseRepository(session, Parameter).add(
        Parameter(type="x", code=uuid.uuid4().hex[:8], name="P")
    )
    company = await BaseRepository(session, ClientCompany).add(ClientCompany(name="GuardCo"))
    contact = await BaseRepository(session, Contact).add(
        Contact(client_company_id=company.id, first_name="C", last_name="D", email="c@d.co")
    )
    dept = await BaseRepository(session, Department).add(Department(name="Eng"))
    process = await BaseRepository(session, Process).add(
        Process(
            client_company_id=company.id,
            department_id=dept.id,
            name=f"P{uuid.uuid4().hex[:6]}",
        )
    )
    template = await BaseRepository(session, ProfileTemplate).add(ProfileTemplate(name="Tmpl"))
    return sp, company, contact, dept, process, template


async def _vacancy(session, sp, company, contact, dept, process, template, status_code):
    status = await _status(session, status_code)
    return await BaseRepository(session, Vacancy).add(
        Vacancy(
            vacancy_name_id=sp.id,
            client_company_id=company.id,
            contact_id=contact.id,
            department_id=dept.id,
            process_id=process.id,
            career_id=sp.id,
            city_id=sp.id,
            work_mode_id=sp.id,
            resource_level_id=sp.id,
            status_id=status.id,
            profile_template_id=template.id,
        )
    )


# ── Contact ─────────────────────────────────────────────────────────────────


async def test_contact_delete_blocked_by_active_vacancy(session: AsyncSession) -> None:
    sp, company, contact, dept, process, template = await _graph(session)
    await _vacancy(session, sp, company, contact, dept, process, template, "active")
    with pytest.raises(ContactInUseError):
        await _contact_service(session).delete(contact.id)


async def test_contact_delete_allowed_when_vacancy_closed(session: AsyncSession) -> None:
    sp, company, contact, dept, process, template = await _graph(session)
    await _vacancy(session, sp, company, contact, dept, process, template, "closed")
    await _contact_service(session).delete(contact.id)
    assert (await session.get(Contact, contact.id)).is_active is False


# ── Department ──────────────────────────────────────────────────────────────


async def test_department_delete_blocked_by_active_process(session: AsyncSession) -> None:
    sp, company, contact, dept, process, template = await _graph(session)
    # No vacancy — only an active process references the department.
    with pytest.raises(DepartmentInUseError):
        await _department_service(session).delete(dept.id)


async def test_department_delete_blocked_by_active_vacancy(session: AsyncSession) -> None:
    sp, company, contact, dept, process, template = await _graph(session)
    await _vacancy(session, sp, company, contact, dept, process, template, "active")
    with pytest.raises(DepartmentInUseError):
        await _department_service(session).delete(dept.id)


# ── Client company ──────────────────────────────────────────────────────────


async def test_client_delete_blocked_by_active_contact(session: AsyncSession) -> None:
    sp, company, contact, dept, process, template = await _graph(session)
    with pytest.raises(ClientCompanyInUseError):
        await _client_service(session).delete(company.id)


async def test_client_delete_allowed_when_empty(session: AsyncSession) -> None:
    company = await BaseRepository(session, ClientCompany).add(ClientCompany(name="EmptyCo"))
    await _client_service(session).delete(company.id)
    assert (await session.get(ClientCompany, company.id)).is_active is False


# ── Profile template ────────────────────────────────────────────────────────


async def test_template_delete_blocked_by_active_vacancy(session: AsyncSession) -> None:
    sp, company, contact, dept, process, template = await _graph(session)
    await _vacancy(session, sp, company, contact, dept, process, template, "active")
    with pytest.raises(ProfileTemplateInUseError):
        await _template_service(session).delete(template.id)


async def test_template_delete_allowed_when_unused(session: AsyncSession) -> None:
    template = await BaseRepository(session, ProfileTemplate).add(ProfileTemplate(name="Free"))
    await _template_service(session).delete(template.id)
    assert (await session.get(ProfileTemplate, template.id)).is_active is False
