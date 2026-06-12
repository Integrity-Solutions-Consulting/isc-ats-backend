from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProcessBase(BaseModel):
    client_company_id: int = Field(examples=[1])
    department_id: int = Field(examples=[1])
    name: str = Field(max_length=150, examples=["Proceso desarrollo backend"])
    description: str | None = None


class ProcessCreate(ProcessBase):
    pass


class ProcessUpdate(BaseModel):
    client_company_id: int | None = None
    department_id: int | None = None
    name: str | None = Field(default=None, max_length=150)
    description: str | None = None
    is_active: bool | None = None


class ProcessRead(ProcessBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool
    created_at: datetime
