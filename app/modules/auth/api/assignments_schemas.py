from pydantic import BaseModel, Field


class RoleAssignment(BaseModel):
    """Body for assigning a role to a user."""

    role_id: int = Field(examples=[1])


class PermissionGrant(BaseModel):
    """Body for granting a permission to a role."""

    permission_id: int = Field(examples=[1])
