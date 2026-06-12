from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ApplicationDocumentCreate(BaseModel):
    application_id: int
    status_id: int
    file_id: int | None = None


class ApplicationDocumentUpdate(BaseModel):
    status_id: int | None = None
    file_id: int | None = None


class ApplicationDocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    application_id: int
    status_id: int
    file_id: int | None = None
    is_active: bool
    created_at: datetime
