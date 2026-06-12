from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class MenuItemBase(BaseModel):
    portal_id: int = Field(examples=[1])
    label: str = Field(max_length=100, examples=["Vacancies"])
    order: int = Field(examples=[10])
    parent_id: int | None = Field(default=None, examples=[None])
    route: str | None = Field(default=None, max_length=200, examples=["/vacancies"])
    icon: str | None = Field(default=None, max_length=50, examples=["briefcase"])
    permission_id: int | None = Field(
        default=None,
        description="When set, the item is shown only to users holding this permission.",
    )


class MenuItemCreate(MenuItemBase):
    pass


class MenuItemUpdate(BaseModel):
    label: str | None = Field(default=None, max_length=100)
    order: int | None = None
    parent_id: int | None = None
    route: str | None = Field(default=None, max_length=200)
    icon: str | None = Field(default=None, max_length=50)
    permission_id: int | None = None


class MenuItemRead(MenuItemBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool
    created_at: datetime


class MenuNode(BaseModel):
    """A menu entry with its visible children — the shape the frontend renders."""

    id: int
    label: str
    order: int
    route: str | None
    icon: str | None
    children: list["MenuNode"]
