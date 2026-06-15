from pydantic import BaseModel, EmailStr, Field


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
    password: str = Field(min_length=6, max_length=72)


class VerifyRequest(BaseModel):
    token: str


class ResendVerificationRequest(BaseModel):
    email: EmailStr

