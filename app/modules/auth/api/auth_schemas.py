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


def _require_terms_acceptance(value: bool) -> bool:
    # Terms + privacy-policy acceptance is mandatory to register (marketing-consent
    # R1). Unlike the marketing flag, this one cannot be False past this point.
    if not value:
        raise ValueError(
            "Debes aceptar los Términos y Condiciones y la Política de Privacidad "
            "para registrarte."
        )
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
    # Mandatory: the "Crea tu cuenta" checkbox is required client-side, and this
    # validator enforces it server-side too (marketing-consent R1). No default —
    # a client that forgets to send it must fail loudly, not silently pass.
    accepts_terms: bool
    # Optional, defaults to False ("not decided yet"). See design D3: an
    # unchecked box must never be recorded as a refusal.
    accepts_marketing: bool = False

    _validate_password = field_validator("password")(_enforce_password_policy)
    _validate_email = field_validator("email")(_reject_disposable_email)
    _validate_terms = field_validator("accepts_terms")(_require_terms_acceptance)


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

