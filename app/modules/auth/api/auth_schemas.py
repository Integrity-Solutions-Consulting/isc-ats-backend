from pydantic import BaseModel, EmailStr, Field, field_validator

from app.shared.validators import password_policy_error


def _enforce_password_policy(value: str) -> str:
    error = password_policy_error(value)
    if error:
        raise ValueError(error)
    return value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=72)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class TokenResponse(BaseModel):
    """Login / refresh response.

    `portal` is the catalog CODE (hr | candidate). The frontend maps it to a
    route — the backend never owns the destination URL.
    """

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    portal: str
    must_change_password: bool
    has_profile: bool = True


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(max_length=72)

    _validate_password = field_validator("password")(_enforce_password_policy)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=72)
    new_password: str = Field(max_length=72)

    _validate_new_password = field_validator("new_password")(_enforce_password_policy)


class VerifyRequest(BaseModel):
    token: str


class ResendVerificationRequest(BaseModel):
    email: EmailStr


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(max_length=72)

    _validate_new_password = field_validator("new_password")(_enforce_password_policy)

