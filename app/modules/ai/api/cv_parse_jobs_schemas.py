from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class CvParseJobCreate(BaseModel):
    file_id: int
    candidate_id: int
    status_id: int
    model_used: str | None = None


class CvParseJobUpdate(BaseModel):
    status_id: int | None = None
    model_used: str | None = None
    result: dict[str, Any] | None = None
    error_detail: str | None = None
    completed_at: datetime | None = None


class CvParseJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    file_id: int
    candidate_id: int
    status_id: int
    model_used: str | None
    result: dict[str, Any] | None
    error_detail: str | None
    completed_at: datetime | None
    is_active: bool
    created_at: datetime
    created_by: int | None
