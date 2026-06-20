from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.shared.schema_mixins import StripRequiredTextMixin


class ClientCompanyBase(BaseModel):
    name: str = Field(max_length=200, examples=["Integrity S.A."])
    legal_name: str | None = Field(default=None, max_length=300)


class ClientCompanyCreate(ClientCompanyBase, StripRequiredTextMixin):
    pass


class ClientCompanyUpdate(StripRequiredTextMixin):
    name: str | None = Field(default=None, max_length=200)
    legal_name: str | None = Field(default=None, max_length=300)


class ClientCompanyRead(ClientCompanyBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool
    created_at: datetime
