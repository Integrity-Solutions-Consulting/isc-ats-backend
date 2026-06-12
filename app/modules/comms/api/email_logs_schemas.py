from datetime import datetime

from pydantic import BaseModel, ConfigDict


class EmailLogCreate(BaseModel):
    to_email: str
    subject: str | None = None
    status_id: int
    provider_message_id: str | None = None
    error_detail: str | None = None


class EmailLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    to_email: str
    subject: str | None
    status_id: int
    provider_message_id: str | None
    error_detail: str | None
    is_active: bool
    created_at: datetime
    created_by: int | None
