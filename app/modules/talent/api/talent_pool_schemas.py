from datetime import datetime

from pydantic import BaseModel, ConfigDict


class TalentPoolCreate(BaseModel):
    candidate_id: int
    source_vacancy_id: int | None = None


class TalentPoolRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    candidate_id: int
    source_vacancy_id: int | None
    is_active: bool
    created_at: datetime
    created_by: int | None
