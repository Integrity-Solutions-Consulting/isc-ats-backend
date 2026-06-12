from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ParameterBase(BaseModel):
    type: str = Field(max_length=50, examples=["vacancy_status"])
    code: str = Field(max_length=100, examples=["active"])
    name: str = Field(max_length=200, examples=["Activo"])


class ParameterCreate(ParameterBase):
    pass


class ParameterUpdate(BaseModel):
    is_active: bool | None = None
    type: str | None = Field(default=None, max_length=50)
    code: str | None = Field(default=None, max_length=100)
    name: str | None = Field(default=None, max_length=200)


class ParameterRead(ParameterBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool
    created_at: datetime
