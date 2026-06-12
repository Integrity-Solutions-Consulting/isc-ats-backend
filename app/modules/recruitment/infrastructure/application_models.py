from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Identity,
    Numeric,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.base_model import Base
from app.shared.mixins import AuditMixin, SoftDeleteMixin


def _fk(target: str) -> ForeignKey:
    return ForeignKey(target, deferrable=True, initially="IMMEDIATE")


class Application(Base, AuditMixin, SoftDeleteMixin):
    """recruitment.applications — a candidate's application to a vacancy.

    One application per (vacancy, candidate). `current_stage_id` is the Kanban
    column; `current_status_id` (stage_status) is the sub-state within it;
    `status_id` (application_status) is the overall outcome. `match_score` /
    `match_summary` are computed on-demand by AI, not set through the CRUD.
    """

    __tablename__ = "applications"
    __table_args__ = (
        UniqueConstraint(
            "vacancy_id", "candidate_id", name="uq_applications_vacancy_id_candidate_id"
        ),
        {"schema": "recruitment"},
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    vacancy_id: Mapped[int] = mapped_column(_fk("recruitment.vacancies.id"))
    candidate_id: Mapped[int] = mapped_column(_fk("recruitment.candidates.id"))
    current_stage_id: Mapped[int | None] = mapped_column(
        _fk("org.process_stages.id"), default=None
    )
    current_status_id: Mapped[int | None] = mapped_column(
        _fk("org.parameters.id"), default=None
    )
    status_id: Mapped[int] = mapped_column(_fk("org.parameters.id"))
    salary_expectation: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), default=None)
    match_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), default=None)
    match_summary: Mapped[str | None] = mapped_column(Text, default=None)
    applied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ApplicationDocument(Base, AuditMixin, SoftDeleteMixin):
    """recruitment.application_documents — a generated document for an application.

    `status_id` is a doc_generation_status parameter; `file_id` points at the
    produced storage.files object once generation completes.
    """

    __tablename__ = "application_documents"
    __table_args__ = {"schema": "recruitment"}

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    application_id: Mapped[int] = mapped_column(
        _fk("recruitment.applications.id"), index=True
    )
    file_id: Mapped[int | None] = mapped_column(_fk("storage.files.id"), default=None)
    status_id: Mapped[int] = mapped_column(_fk("org.parameters.id"))


class ApplicationNote(Base, AuditMixin, SoftDeleteMixin):
    """recruitment.application_notes — a free-text note attached to an application."""

    __tablename__ = "application_notes"
    __table_args__ = {"schema": "recruitment"}

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    application_id: Mapped[int] = mapped_column(
        _fk("recruitment.applications.id"), index=True
    )
    content: Mapped[str] = mapped_column(Text)
