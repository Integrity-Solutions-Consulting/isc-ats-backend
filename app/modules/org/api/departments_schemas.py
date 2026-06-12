from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DepartmentBase(BaseModel):
    name: str = Field(max_length=150, examples=["Tecnología"])
    description: str | None = Field(default=None, examples=["Área de desarrollo"])


class DepartmentCreate(DepartmentBase):
    pass


class DepartmentUpdate(BaseModel):
    is_active: bool | None = None
    name: str | None = Field(default=None, max_length=150)
    description: str | None = None


class DepartmentRead(DepartmentBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool
    created_at: datetime
