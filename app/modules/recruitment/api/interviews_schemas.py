from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class InterviewBase(BaseModel):
    application_id: int
    process_stage_id: int
    interviewer_id: int = Field(description="auth.users id of the interviewer")
    scheduled_at: datetime
    ends_at: datetime
    status_id: int = Field(description="org.parameters (type=interview_status)")
    scheduled_by_id: int = Field(description="org.parameters (type=interview_scheduler)")
    extra_email: str | None = Field(default=None, max_length=255)
    offered_slots: list[dict[str, Any]] | None = None
    slot_selection_token: str | None = Field(default=None, max_length=64)
    teams_meeting_url: str | None = Field(default=None, max_length=500)
    teams_meeting_id: str | None = Field(default=None, max_length=200)
    cancellation_reason: str | None = None


class InterviewCreate(InterviewBase):
    pass


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
