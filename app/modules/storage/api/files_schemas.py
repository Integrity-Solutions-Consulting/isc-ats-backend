from datetime import datetime

from pydantic import BaseModel, ConfigDict


class FileCreate(BaseModel):
    original_name: str
    stored_key: str
    bucket: str
    mime_type: str | None = None
    size_bytes: int | None = None
    is_public: bool = False
    entity_type: str | None = None
    entity_id: int | None = None


class FileUpdate(BaseModel):
    entity_type: str | None = None
    entity_id: int | None = None
    is_public: bool | None = None


class FileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    original_name: str
    stored_key: str
    bucket: str
    mime_type: str | None
    size_bytes: int | None
    is_public: bool
    entity_type: str | None
    entity_id: int | None
    is_active: bool
    created_at: datetime
    created_by: int | None
