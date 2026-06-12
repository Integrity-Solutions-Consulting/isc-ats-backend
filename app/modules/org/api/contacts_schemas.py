from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class ContactBase(BaseModel):
    client_company_id: int = Field(examples=[1])
    first_name: str = Field(max_length=100, examples=["María"])
    last_name: str = Field(max_length=100, examples=["Vélez"])
    email: EmailStr = Field(examples=["maria.velez@empresa.com"])


class ContactCreate(ContactBase):
    pass


class ContactUpdate(BaseModel):
    client_company_id: int | None = None
    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    email: EmailStr | None = None


class ContactRead(ContactBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool
    created_at: datetime
