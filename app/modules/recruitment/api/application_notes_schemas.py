from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ApplicationNoteCreate(BaseModel):
    application_id: int
    content: str = Field(min_length=1)


class ApplicationNoteUpdate(BaseModel):
    content: str | None = Field(default=None, min_length=1)


def _author_name_from_email(email: str | None) -> str:
    """Derive a display name from an email address.

    Examples:
      nombre.apellido@integritysolutions.com.ec -> "Nombre Apellido"
      juan@otherdomain.com                      -> "Juan"
      None or ""                                -> "Staff"
    """
    if not email:
        return "Staff"
    local = email.split("@")[0]
    parts = local.split(".")
    return " ".join(p.capitalize() for p in parts if p)


class ApplicationNoteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    application_id: int
    content: str
    is_active: bool
    created_at: datetime
    created_by: int | None = None
    author_name: str = "Staff"
