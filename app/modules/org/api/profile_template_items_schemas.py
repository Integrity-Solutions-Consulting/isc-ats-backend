from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProfileTemplateItemBase(BaseModel):
    template_id: int = Field(examples=[1])
    category_id: int = Field(
        examples=[1], description="org.parameters id of type 'template_item_category'"
    )
    name: str = Field(max_length=300, examples=["C#", "Angular", "Comunicación asertiva"])


class ProfileTemplateItemCreate(ProfileTemplateItemBase):
    pass


class ProfileTemplateItemUpdate(BaseModel):
    category_id: int | None = None
    name: str | None = Field(default=None, max_length=300)


class ProfileTemplateItemRead(ProfileTemplateItemBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool
    created_at: datetime
