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
    # Free text HR writes when moving the candidate to the rejected Kanban column
    # (current_stage_id=None). Required by the service when that move actually
    # results in a rejected outcome — see ApplicationService.update.
    rejection_reason: str | None = None


class ApplicationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    vacancy_id: int
    candidate_id: int
    status_id: int
    current_stage_id: int | None = None
    current_status_id: int | None = None
    rejected_at_stage_id: int | None = None
    rejection_reason: str | None = None
    # Resolved by ApplicationService.list()/get() regardless of the vacancy's
    # current status (draft/active/closed) — only a hard-deleted (is_active=False)
    # vacancy resolves to None. Lets a candidate's applications list show the real
    # vacancy title even after the vacancy closes, instead of relying on the
    # PUBLIC catalog (active-only) to resolve the name.
    vacancy_name: str | None = None
    salary_expectation: Decimal | None = None
    # AI-managed, read-only.
    match_score: Decimal | None = None
    match_summary: str | None = None
    applied_at: datetime
    is_active: bool
    created_at: datetime
