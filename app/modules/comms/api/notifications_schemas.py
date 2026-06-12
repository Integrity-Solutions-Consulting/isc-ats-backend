from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class NotificationCreate(BaseModel):
    recipient_id: int
    title: str
    body: str | None = None
    channel_id: int | None = None
    related_entity_type: str | None = None
    related_entity_id: int | None = None
    metadata_: dict[str, Any] | None = None


class NotificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: int
    recipient_id: int
    title: str
    body: str | None
    channel_id: int | None
    related_entity_type: str | None
    related_entity_id: int | None
    metadata_: dict[str, Any] | None
    read_at: datetime | None
    is_active: bool
    created_at: datetime
    created_by: int | None
