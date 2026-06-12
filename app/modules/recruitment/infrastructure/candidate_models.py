from datetime import date, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import Date, DateTime, ForeignKey, Identity, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.base_model import Base
from app.shared.mixins import AuditMixin, SoftDeleteMixin

# Embedding dimension for CV vectors (matches schema.sql vector(1536)).
CV_EMBEDDING_DIM = 1536


def _fk(target: str) -> ForeignKey:
    return ForeignKey(target, deferrable=True, initially="IMMEDIATE")


class Candidate(Base, AuditMixin, SoftDeleteMixin):
    """recruitment.candidates — a person in the talent pipeline, 1:1 with a user.

    `parsed_data` (jsonb) and `cv_embedding` (pgvector) are populated by the AI
    CV-parsing pipeline, not by the candidate CRUD. File references are nullable
    FKs into storage.files.
    """

    __tablename__ = "candidates"
    __table_args__ = {"schema": "recruitment"}

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(_fk("auth.users.id"), unique=True)
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    cedula: Mapped[str | None] = mapped_column(String(20), unique=True, default=None)
    birth_date: Mapped[date | None] = mapped_column(Date, default=None)
    phone: Mapped[str | None] = mapped_column(String(20), default=None)
    city_id: Mapped[int | None] = mapped_column(_fk("org.parameters.id"), default=None)
    province_id: Mapped[int | None] = mapped_column(_fk("org.parameters.id"), default=None)
    avatar_file_id: Mapped[int | None] = mapped_column(_fk("storage.files.id"), default=None)
    education_level_id: Mapped[int | None] = mapped_column(
        _fk("org.parameters.id"), default=None
    )
    career_id: Mapped[int | None] = mapped_column(_fk("org.parameters.id"), default=None)
    university_id: Mapped[int | None] = mapped_column(_fk("org.parameters.id"), default=None)
    home_address: Mapped[str | None] = mapped_column(String(300), default=None)
    is_studying: Mapped[bool] = mapped_column(default=False)
    is_working: Mapped[bool] = mapped_column(default=False)
    current_company: Mapped[str | None] = mapped_column(String(200), default=None)
    cv_file_id: Mapped[int | None] = mapped_column(_fk("storage.files.id"), default=None)
    parsed_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    cv_embedding: Mapped[list[float] | None] = mapped_column(
        Vector(CV_EMBEDDING_DIM), default=None
    )
    last_parsed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
