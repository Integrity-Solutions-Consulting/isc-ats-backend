from pydantic import BaseModel, EmailStr, Field, field_validator

from app.modules.auth.application.disposable_email import is_disposable_email
from app.shared.validators import password_policy_error


def _enforce_password_policy(value: str) -> str:
    error = password_policy_error(value)
    if error:
        raise ValueError(error)
    return value


def _reject_disposable_email(value: str) -> str:
    # Registration is the abuse funnel; refuse throwaway domains (yopmail et al.).
    if is_disposable_email(value):
        raise ValueError("Los correos temporales o desechables no están permitidos.")
    return value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=72)
    # Cloudflare Turnstile token from the widget. Optional so the field is inert
    # when the gate is disabled; verified server-side when enabled.
    turnstile_token: str | None = None


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
    # Cloudflare Turnstile token from the widget. Optional so the field is inert
    # when the gate is disabled; verified server-side (fail-closed) when enabled.
    turnstile_token: str | None = None

    _validate_password = field_validator("password")(_enforce_password_policy)
    _validate_email = field_validator("email")(_reject_disposable_email)


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

