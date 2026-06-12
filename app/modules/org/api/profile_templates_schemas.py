from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProfileTemplateBase(BaseModel):
    name: str = Field(max_length=200, examples=["Backend .NET Senior"])


class ProfileTemplateCreate(ProfileTemplateBase):
    pass


class ProfileTemplateUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    is_active: bool | None = None


class ProfileTemplateRead(ProfileTemplateBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool
    created_at: datetime
