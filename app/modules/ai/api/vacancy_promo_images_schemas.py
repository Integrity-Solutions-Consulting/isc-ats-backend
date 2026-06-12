from datetime import datetime

from pydantic import BaseModel, ConfigDict


class VacancyPromoImageCreate(BaseModel):
    vacancy_id: int
    file_id: int
    template_used: str | None = None


class VacancyPromoImageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    vacancy_id: int
    file_id: int
    template_used: str | None
    is_active: bool
    created_at: datetime
    created_by: int | None
