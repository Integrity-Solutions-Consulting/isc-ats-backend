from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RoleBase(BaseModel):
    name: str = Field(max_length=100, examples=["Recruiter"])
    description: str | None = Field(default=None, examples=["Manages vacancies and candidates"])


class RoleCreate(RoleBase):
    pass


class RoleUpdate(BaseModel):
    # name is nullable only in the sense of "omit to leave unchanged"; an explicit
    # null (or empty string) would write into a NOT NULL column and 500, so reject
    # it at the schema boundary with min_length=1.
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _reject_explicit_null_name(cls, data: object) -> object:
        # `name` may be OMITTED (leave unchanged), but an explicit null targets a
        # NOT NULL column and would 500 on flush. min_length can't catch None on an
        # Optional field, so reject the explicit null here.
        if isinstance(data, dict) and "name" in data and data["name"] is None:
            raise ValueError("name cannot be null")
        return data


class RoleRead(RoleBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool
    created_at: datetime
