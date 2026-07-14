from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class InterviewBase(BaseModel):
    application_id: int
    process_stage_id: int
    interviewer_id: int = Field(description="auth.users id of the interviewer")
    scheduled_at: datetime | None = None
    ends_at: datetime | None = None
    status_id: int = Field(description="org.parameters (type=interview_status)")
    scheduled_by_id: int = Field(description="org.parameters (type=interview_scheduler)")
    extra_email: str | None = Field(default=None, max_length=255)
    offered_slots: list[dict[str, Any]] | None = None
    slot_selection_token: str | None = Field(default=None, max_length=64)
    token_expires_at: datetime | None = None
    teams_meeting_url: str | None = Field(default=None, max_length=500)
    teams_meeting_id: str | None = Field(default=None, max_length=200)
    cancellation_reason: str | None = None


class InterviewCreate(InterviewBase):
    """Mode A: direct scheduling. scheduled_at and ends_at are required."""

    scheduled_at: datetime
    ends_at: datetime


class InterviewUpdate(BaseModel):
    process_stage_id: int | None = None
    interviewer_id: int | None = None
    scheduled_at: datetime | None = None
    ends_at: datetime | None = None
    status_id: int | None = None
    scheduled_by_id: int | None = None
    extra_email: str | None = Field(default=None, max_length=255)
    offered_slots: list[dict[str, Any]] | None = None
    slot_selection_token: str | None = Field(default=None, max_length=64)
    teams_meeting_url: str | None = Field(default=None, max_length=500)
    teams_meeting_id: str | None = Field(default=None, max_length=200)
    cancellation_reason: str | None = None


class InterviewRead(InterviewBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool
    created_at: datetime


# ── Slot-selection schemas ────────────────────────────────────────────────────


class SlotRead(BaseModel):
    """A single available interview slot (UTC ISO-8601 datetime)."""

    start: datetime
    end: datetime


class InterviewerRead(BaseModel):
    """Minimal user projection for the interviewers dropdown."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str


# ── Mode B (candidate self-scheduling) schemas ────────────────────────────────


class InterviewInviteCreate(BaseModel):
    """Payload for POST /interviews/invite (Mode B).

    HR creates a pending interview and sends the candidate a list of offered
    time slots to choose from.
    """

    application_id: int
    process_stage_id: int
    interviewer_id: int = Field(description="auth.users id of the interviewer")
    offered_slots: list[dict[str, Any]] = Field(
        description="List of {start, end} UTC ISO-8601 objects offered to the candidate"
    )
    extra_email: str | None = Field(default=None, max_length=255)
    subject: str | None = Field(
        default=None,
        max_length=200,
        description="Optional email subject override",
    )


class SlotConfirmRequest(BaseModel):
    """Payload for POST /interviews/slots/{token}/confirm."""

    chosen_slot: dict[str, Any] = Field(
        description="Must match one entry in offered_slots exactly"
    )


# ── Agenda widget (D5) — "Reuniones de hoy y mañana" ──────────────────────────


class AgendaInterviewRead(BaseModel):
    """A single scheduled interview enriched for the today/tomorrow agenda widget.

    Returned by GET /interviews/agenda, gated by recruitment.interviews.read_agenda
    (Admin + Talento Humano only). `day` buckets the entry relative to the Ecuador
    local calendar day, computed server-side.
    """

    id: int
    scheduled_at: datetime
    ends_at: datetime | None = None
    candidate_name: str
    vacancy_name: str
    interviewer_email: str
    teams_meeting_url: str | None = None
    day: str = Field(description='"today" or "tomorrow" (Ecuador local calendar day)')
