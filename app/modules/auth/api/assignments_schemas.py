from pydantic import BaseModel, Field


class RoleAssignment(BaseModel):
    """Body for assigning a role to a user."""

    role_id: int = Field(examples=[1])


class PermissionGrant(BaseModel):
    """Body for granting a permission to a role."""

    permission_id: int = Field(examples=[1])


class ParameterTypesBody(BaseModel):
    """Body/response shape for a role's writable org.parameters TYPE allowlist."""

    parameter_types: list[str] = Field(examples=[["vacancy_name", "stage"]])
