from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PermissionBase(BaseModel):
    code: str = Field(max_length=100, examples=["org.departments.create"])
    name: str = Field(max_length=150, examples=["Create departments"])
    description: str | None = Field(default=None, examples=["Allows creating departments"])
    module: str | None = Field(default=None, max_length=50, examples=["org"])


class PermissionCreate(PermissionBase):
    pass


class PermissionUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=150)
    description: str | None = None
    module: str | None = Field(default=None, max_length=50)


class PermissionRead(PermissionBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool
    created_at: datetime
