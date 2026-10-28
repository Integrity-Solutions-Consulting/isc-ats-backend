from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class TalentPoolCreate(BaseModel):
    candidate_id: int
    source_vacancy_id: int | None = None


class TalentPoolRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    candidate_id: int
    source_vacancy_id: int | None = None
    source_vacancy_title: str | None = None
    is_active: bool
    created_at: datetime
    created_by: int | None = None

    # Candidate enriched profile details
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    phone: str | None = None
    city: str | None = None
    career: str | None = None
    title: str | None = None
    university: str | None = None
    years_of_experience: Decimal | None = None
    current_company: str | None = None
    cv_file_id: int | None = None
    avatar_file_id: int | None = None
    skills: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)