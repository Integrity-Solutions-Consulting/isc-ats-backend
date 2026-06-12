from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Identity, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

# Re-export the sibling models so importing this module registers the whole
# recruitment bounded context on Base.metadata (single registry surface).
from app.modules.recruitment.infrastructure.application_models import (  # noqa: F401
    Application,
    ApplicationDocument,
    ApplicationNote,
)
from app.modules.recruitment.infrastructure.candidate_models import Candidate  # noqa: F401
from app.modules.recruitment.infrastructure.interview_models import (  # noqa: F401
    Interview,
    InterviewerAvailability,
)
from app.shared.base_model import Base
from app.shared.mixins import AuditMixin, SoftDeleteMixin


def _fk(target: str) -> ForeignKey:
    return ForeignKey(target, deferrable=True, initially="IMMEDIATE")


class Vacancy(Base, AuditMixin, SoftDeleteMixin):
    """recruitment.vacancies — a job opening for a client company.

    The richest entity so far: ten required references (six into the
    org.parameters catalog: vacancy_name, career, city, work_mode,
    resource_level, vacancy_status) plus an optional profile_template and a
    free-form `profile_requirements` jsonb snapshot of the requirements.
    """

    __tablename__ = "vacancies"
    __table_args__ = {"schema": "recruitment"}

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    vacancy_name_id: Mapped[int] = mapped_column(_fk("org.parameters.id"))
    client_company_id: Mapped[int] = mapped_column(_fk("org.client_companies.id"))
    contact_id: Mapped[int] = mapped_column(_fk("org.contacts.id"))
    department_id: Mapped[int] = mapped_column(_fk("org.departments.id"))
    process_id: Mapped[int] = mapped_column(_fk("org.processes.id"))
    career_id: Mapped[int] = mapped_column(_fk("org.parameters.id"))
    city_id: Mapped[int] = mapped_column(_fk("org.parameters.id"))
    work_mode_id: Mapped[int] = mapped_column(_fk("org.parameters.id"))
    profile_template_id: Mapped[int | None] = mapped_column(
        _fk("org.profile_templates.id"), default=None
    )
    profile_requirements: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, default=None
    )
    resource_level_id: Mapped[int] = mapped_column(_fk("org.parameters.id"))
    openings: Mapped[int] = mapped_column(default=1)
    experience_years: Mapped[int] = mapped_column(default=0)
    work_schedule: Mapped[str | None] = mapped_column(String(100), default=None)
    project_duration_years: Mapped[int] = mapped_column(default=0)
    project_duration_months: Mapped[int] = mapped_column(default=0)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    status_id: Mapped[int] = mapped_column(_fk("org.parameters.id"))
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
