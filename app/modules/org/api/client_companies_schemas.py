from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ClientCompanyBase(BaseModel):
    name: str = Field(max_length=200, examples=["Integrity S.A."])
    legal_name: str | None = Field(default=None, max_length=300)


class ClientCompanyCreate(ClientCompanyBase):
    pass


class ClientCompanyUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    legal_name: str | None = Field(default=None, max_length=300)


class ClientCompanyRead(ClientCompanyBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool
    created_at: datetime
