from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Identity, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.base_model import Base
from app.shared.mixins import AuditMixin, SoftDeleteMixin


def _fk(target: str) -> ForeignKey:
    return ForeignKey(target, deferrable=True, initially="IMMEDIATE")


class CvParseJob(Base, AuditMixin, SoftDeleteMixin):
    """ai.cv_parse_jobs — tracks an AI CV-parsing run for a candidate.

    Kicked off when a candidate uploads a CV; the pipeline populates `result`
    and sets `status_id` to done/failed. `model_used` records which LLM was
    called. `completed_at` is set when the job finishes (success or error).
    """

    __tablename__ = "cv_parse_jobs"
    __table_args__ = {"schema": "ai"}

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    file_id: Mapped[int] = mapped_column(_fk("storage.files.id"))
    candidate_id: Mapped[int] = mapped_column(_fk("recruitment.candidates.id"))
    status_id: Mapped[int] = mapped_column(_fk("org.parameters.id"))
    model_used: Mapped[str | None] = mapped_column(String(100), default=None)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    error_detail: Mapped[str | None] = mapped_column(Text, default=None)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )


class VacancyPromoImage(Base, AuditMixin, SoftDeleteMixin):
    """ai.vacancy_promo_images — AI-generated promotional image for a vacancy."""

    __tablename__ = "vacancy_promo_images"
    __table_args__ = {"schema": "ai"}

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    vacancy_id: Mapped[int] = mapped_column(_fk("recruitment.vacancies.id"))
    file_id: Mapped[int] = mapped_column(_fk("storage.files.id"))
    template_used: Mapped[str | None] = mapped_column(String(100), default=None)


class AiUsageLog(Base, AuditMixin, SoftDeleteMixin):
    """ai.ai_usage_logs — immutable record of every LLM API call.

    Written by the system; no update semantics. Tracks tokens and cost per
    action so we can audit spend and optimize prompts.
    """

    __tablename__ = "ai_usage_logs"
    __table_args__ = {"schema": "ai"}

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    action: Mapped[str] = mapped_column(String(100))
    model: Mapped[str | None] = mapped_column(String(100), default=None)
    input_tokens: Mapped[int | None] = mapped_column(default=None)
    output_tokens: Mapped[int | None] = mapped_column(default=None)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), default=None)
