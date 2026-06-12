from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class ApplicationCreate(BaseModel):
    vacancy_id: int
    candidate_id: int
    status_id: int
    current_stage_id: int | None = None
    current_status_id: int | None = None
    salary_expectation: Decimal | None = None


class ApplicationUpdate(BaseModel):
    # vacancy_id / candidate_id are identity (unique pair) and are not editable.
    status_id: int | None = None
    current_stage_id: int | None = None
    current_status_id: int | None = None


class ApplicationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    vacancy_id: int
    candidate_id: int
    status_id: int
    current_stage_id: int | None = None
    current_status_id: int | None = None
    salary_expectation: Decimal | None = None
    # AI-managed, read-only.
    match_score: Decimal | None = None
    match_summary: str | None = None
    applied_at: datetime
    is_active: bool
    created_at: datetime
