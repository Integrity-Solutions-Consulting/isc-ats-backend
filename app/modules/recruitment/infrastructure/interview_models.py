from datetime import datetime, time
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Identity, String, Text, Time
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.base_model import Base
from app.shared.mixins import AuditMixin, SoftDeleteMixin


def _fk(target: str) -> ForeignKey:
    return ForeignKey(target, deferrable=True, initially="IMMEDIATE")


class InterviewerAvailability(Base, AuditMixin, SoftDeleteMixin):
    """recruitment.interviewer_availability — a weekly availability window.

    `day_of_week` 0-6, with a recurring [start_time, end_time] slotted into
    `slot_duration_min` chunks for scheduling.
    """

    __tablename__ = "interviewer_availability"
    __table_args__ = {"schema": "recruitment"}

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(_fk("auth.users.id"))
    day_of_week: Mapped[int] = mapped_column()
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)
    slot_duration_min: Mapped[int] = mapped_column(default=60)


class Interview(Base, AuditMixin, SoftDeleteMixin):
    """recruitment.interviews — a scheduled interview for an application.

    `status_id` is an interview_status parameter; `scheduled_by_id` an
    interview_scheduler parameter (hr | candidate), NOT a user. `offered_slots`
    (jsonb) holds the candidate-facing slot options for self-scheduling.
    """

    __tablename__ = "interviews"
    __table_args__ = {"schema": "recruitment"}

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    application_id: Mapped[int] = mapped_column(
        _fk("recruitment.applications.id"), index=True
    )
    process_stage_id: Mapped[int] = mapped_column(_fk("org.process_stages.id"))
    interviewer_id: Mapped[int] = mapped_column(_fk("auth.users.id"))
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    extra_email: Mapped[str | None] = mapped_column(String(255), default=None)
    offered_slots: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, default=None)
    slot_selection_token: Mapped[str | None] = mapped_column(String(64), default=None)
    teams_meeting_url: Mapped[str | None] = mapped_column(String(500), default=None)
    teams_meeting_id: Mapped[str | None] = mapped_column(String(200), default=None)
    status_id: Mapped[int] = mapped_column(_fk("org.parameters.id"))
    scheduled_by_id: Mapped[int] = mapped_column(_fk("org.parameters.id"))
    cancellation_reason: Mapped[str | None] = mapped_column(Text, default=None)
