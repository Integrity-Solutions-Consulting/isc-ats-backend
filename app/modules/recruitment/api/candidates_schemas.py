from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.shared.validators import (
    is_adult,
    is_valid_id_number,
    is_valid_phone_ec,
)


class _CandidateInputValidators(BaseModel):
    """Server-side mirror of the frontend EC validation, applied to input
    schemas only (never to read schemas, so existing rows still serialise).
    `check_fields=False` lets these decorate fields declared on sibling classes."""

    @field_validator("first_name", "last_name", check_fields=False)
    @classmethod
    def _require_non_empty_name(cls, v: str | None) -> str | None:
        if v is None:
            return v
        stripped = v.strip()
        if not stripped:
            raise ValueError("Este campo es requerido")
        return stripped

    @field_validator("cedula", check_fields=False)
    @classmethod
    def _validate_cedula(cls, v: str | None) -> str | None:
        if v is None:
            return v
        stripped = v.strip()
        if not stripped:
            return None
        if not is_valid_id_number(stripped):
            raise ValueError("Cédula o documento de identidad inválido")
        return stripped

    @field_validator("phone", check_fields=False)
    @classmethod
    def _validate_phone(cls, v: str | None) -> str | None:
        if v is None:
            return v
        stripped = v.strip()
        if not stripped:
            return None
        if not is_valid_phone_ec(stripped):
            raise ValueError("Número de celular inválido (ej: 0991234567)")
        return stripped

    @field_validator("birth_date", check_fields=False)
    @classmethod
    def _validate_age(cls, v: date | None) -> date | None:
        if v is None:
            return v
        if not is_adult(v):
            raise ValueError("Debe ser mayor de 18 años")
        return v


class CandidateBase(BaseModel):
    first_name: str = Field(max_length=100)
    last_name: str = Field(max_length=100)
    cedula: str | None = Field(default=None, max_length=20)
    birth_date: date | None = None
    phone: str | None = Field(default=None, max_length=20)
    city_id: int | None = None
    avatar_file_id: int | None = None
    education_level_id: int | None = None
    career_id: int | None = None
    title_id: int | None = None
    university_id: int | None = None
    home_address: str | None = Field(default=None, max_length=300)
    is_studying: bool = False
    is_working: bool = False
    current_company: str | None = Field(default=None, max_length=200)
    cv_file_id: int | None = None


class CandidateCreate(CandidateBase, _CandidateInputValidators):
    user_id: int = Field(description="auth.users id — one candidate per user")


class CandidateUpdate(_CandidateInputValidators):
    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    cedula: str | None = Field(default=None, max_length=20)
    birth_date: date | None = None
    phone: str | None = Field(default=None, max_length=20)
    city_id: int | None = None
    avatar_file_id: int | None = None
    education_level_id: int | None = None
    career_id: int | None = None
    title_id: int | None = None
    university_id: int | None = None
    home_address: str | None = Field(default=None, max_length=300)
    is_studying: bool | None = None
    is_working: bool | None = None
    current_company: str | None = Field(default=None, max_length=200)
    cv_file_id: int | None = None


class CandidateRead(CandidateBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    # AI-managed fields are read-only here; cv_embedding is intentionally omitted.
    parsed_data: dict[str, Any] | None = None
    last_parsed_at: datetime | None = None
    is_active: bool
    created_at: datetime


class CvPrefillResponse(BaseModel):
    firstName: str | None = None
    lastName: str | None = None
    idNumber: str | None = None
    birthDate: str | None = None
    phone: str | None = None
    homeAddress: str | None = None
    currentCompany: str | None = None
    cityId: int | None = None
    educationLevelId: int | None = None
    careerId: int | None = None
    titleId: int | None = None
    universityId: int | None = None


class CandidateExpandedRead(BaseModel):
    """Expanded candidate read — all FK fields resolved to human-readable labels."""

    model_config = ConfigDict(from_attributes=False)

    id: int
    user_id: int
    email: str
    first_name: str
    last_name: str
    cedula: str | None
    birth_date: date | None
    phone: str | None
    city: str | None
    education_level: str | None
    career: str | None
    title: str | None
    university: str | None
    home_address: str | None
    is_studying: bool
    is_working: bool
    current_company: str | None
    cv_file_id: int | None
    avatar_file_id: int | None
    is_active: bool
    created_at: datetime


class CatalogOption(BaseModel):
    id: int
    code: str
    name: str


class RegistrationCatalogResponse(BaseModel):
    cities: list[CatalogOption]
    educationLevels: list[CatalogOption]
    careers: list[CatalogOption]
    titles: list[CatalogOption]
    universities: list[CatalogOption]
