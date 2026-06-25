from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class VacancyBase(BaseModel):
    vacancy_name_id: int = Field(description="org.parameters (type=vacancy_name)")
    client_company_id: int
    contact_id: int
    department_id: int
    process_id: int
    career_id: int = Field(description="org.parameters (type=career)")
    city_id: int = Field(description="org.parameters (type=city)")
    work_mode_id: int = Field(description="org.parameters (type=work_mode)")
    resource_level_id: int = Field(description="org.parameters (type=resource_level)")
    status_id: int = Field(description="org.parameters (type=vacancy_status)")
    profile_template_id: int | None = None
    profile_requirements: dict[str, Any] | None = None
    openings: int = Field(default=1, ge=1)
    experience_years: int = Field(default=0, ge=0)
    work_schedule: str | None = Field(default=None, max_length=100)
    project_duration_years: int = Field(default=0, ge=0)
    project_duration_months: int = Field(default=0, ge=0)
    description: str | None = None
    published_at: datetime | None = None


class VacancyCreate(VacancyBase):
    pass


class VacancyUpdate(BaseModel):
    is_active: bool | None = None
    vacancy_name_id: int | None = None
    client_company_id: int | None = None
    contact_id: int | None = None
    department_id: int | None = None
    process_id: int | None = None
    career_id: int | None = None
    city_id: int | None = None
    work_mode_id: int | None = None
    resource_level_id: int | None = None
    status_id: int | None = None
    profile_template_id: int | None = None
    profile_requirements: dict[str, Any] | None = None
    openings: int | None = Field(default=None, ge=1)
    experience_years: int | None = Field(default=None, ge=0)
    work_schedule: str | None = Field(default=None, max_length=100)
    project_duration_years: int | None = Field(default=None, ge=0)
    project_duration_months: int | None = Field(default=None, ge=0)
    description: str | None = None
    published_at: datetime | None = None


class VacancyRead(VacancyBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool
    created_at: datetime


class VacancyListItem(BaseModel):
    """Expanded vacancy read — all FK fields resolved to human-readable labels."""

    model_config = ConfigDict(from_attributes=False)

    id: int
    vacancy_name: str
    client_company: str
    contact_id: int
    contact: str
    department: str
    process: str
    career: str
    city: str
    work_mode: str
    resource_level: str
    vacancy_status: str
    openings: int
    experience_years: int
    work_schedule: str | None
    project_duration_years: int
    project_duration_months: int
    description: str | None
    profile_requirements: dict[str, Any] | None
    is_active: bool
    created_at: datetime


class PublicVacancyItem(BaseModel):
    """Public-safe expanded vacancy — omits client company and internal contact info."""

    model_config = ConfigDict(from_attributes=False)

    id: int
    vacancy_name: str
    career: str
    city: str
    work_mode: str
    resource_level: str
    openings: int
    experience_years: int
    work_schedule: str | None
    project_duration_years: int
    project_duration_months: int
    description: str | None
    profile_requirements: dict[str, Any] | None
    created_at: datetime


# ── Pipeline schemas ──────────────────────────────────────────────────────────


class VacancyStageItem(BaseModel):
    """Lightweight stage item for the candidate-facing stages endpoint."""

    id: int
    name: str
    order: int
    is_final_positive: bool
    is_initial: bool = False


class PipelineStageSchema(BaseModel):
    id: str
    vacancyId: str
    name: str
    order: int
    type: Literal["normal", "final", "rejected"] = "normal"


class PipelineCardSchema(BaseModel):
    id: str
    candidateId: str
    vacancyId: str
    stageId: str
    candidateName: str
    initials: str
    avatarColor: str
    avatarFileId: int | None = None
    matchPercent: float | None
    matchStatus: Literal["analyzing", "done"] = "analyzing"
    stageStatus: str = "pending_review"
    salaryExpectation: int = 0
    updatedAt: str


class PipelineSchema(BaseModel):
    stages: list[PipelineStageSchema]
    cards: list[PipelineCardSchema]
    rejectionSummary: dict[str, Any]
    hiredCount: int = 0
    openings: int = 0


# ── Vacancy document schemas ──────────────────────────────────────────────────


class VacancyDocumentItem(BaseModel):
    """One generated Word profile document associated with a vacancy's candidate."""

    id: int                     # application_documents.id
    application_id: int
    candidate_id: int
    candidate_name: str
    candidate_initials: str
    candidate_avatar_color: str
    stage_name_at_generation: str
    file_name: str
    file_id: int | None
    stored_key: str | None
    version: int
    generated_by: str           # author display name (email → "Nombre Apellido")
    generated_at: datetime
