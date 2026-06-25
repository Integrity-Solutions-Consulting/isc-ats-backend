from sqlalchemy import ForeignKey, Identity, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.base_model import Base
from app.shared.mixins import AuditMixin, SoftDeleteMixin


class Parameter(Base, AuditMixin, SoftDeleteMixin):
    """org.parameters — polymorphic catalog (type + code + name).

    Almost every lookup FK in the system points here (portal_id, status_id,
    city_id, career_id, work_mode_id, ...). Seeding it is a prerequisite for
    everything else. Uniqueness is per (type, code).
    """

    __tablename__ = "parameters"
    __table_args__ = (
        UniqueConstraint("type", "code", name="uq_parameters_type_code"),
        {"schema": "org"},
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    type: Mapped[str] = mapped_column(String(50), index=True)
    code: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))


class Department(Base, AuditMixin, SoftDeleteMixin):
    """org.departments — plain catalog of departments (thin CRUD)."""

    __tablename__ = "departments"
    __table_args__ = {"schema": "org"}

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String(150))
    description: Mapped[str | None] = mapped_column(Text, default=None)


class ClientCompany(Base, AuditMixin, SoftDeleteMixin):
    """org.client_companies — companies we recruit for (thin CRUD)."""

    __tablename__ = "client_companies"
    __table_args__ = {"schema": "org"}

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    legal_name: Mapped[str | None] = mapped_column(String(300), default=None)


class Contact(Base, AuditMixin, SoftDeleteMixin):
    """org.contacts — a person at a client company (thin CRUD, FK to the company)."""

    __tablename__ = "contacts"
    __table_args__ = {"schema": "org"}

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    client_company_id: Mapped[int] = mapped_column(
        ForeignKey("org.client_companies.id", deferrable=True, initially="IMMEDIATE"),
        index=True,
    )
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(255), index=True)


class Process(Base, AuditMixin, SoftDeleteMixin):
    """org.processes — a hiring process for a client company + department.

    Unique per (client_company_id, department_id, name). Its ordered stages live
    in org.process_stages.
    """

    __tablename__ = "processes"
    __table_args__ = (
        UniqueConstraint(
            "client_company_id",
            "department_id",
            "name",
            name="uq_processes_client_company_id_department_id_name",
        ),
        {"schema": "org"},
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    client_company_id: Mapped[int] = mapped_column(
        ForeignKey("org.client_companies.id", deferrable=True, initially="IMMEDIATE")
    )
    department_id: Mapped[int] = mapped_column(
        ForeignKey("org.departments.id", deferrable=True, initially="IMMEDIATE")
    )
    name: Mapped[str] = mapped_column(String(150))
    description: Mapped[str | None] = mapped_column(Text, default=None)


class ProcessStage(Base, AuditMixin, SoftDeleteMixin):
    """org.process_stages — an ordered stage within a hiring process.

    `stage_id` points at org.parameters (type=stage). Unique per (process, stage)
    and per (process, order). `is_final_positive` marks the hiring stage.
    """

    __tablename__ = "process_stages"
    __table_args__ = (
        UniqueConstraint(
            "process_id", "stage_id", name="uq_process_stages_process_id_stage_id"
        ),
        UniqueConstraint(
            "process_id", "order", name="uq_process_stages_process_id_order"
        ),
        {"schema": "org"},
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    process_id: Mapped[int] = mapped_column(
        ForeignKey("org.processes.id", deferrable=True, initially="IMMEDIATE"),
        index=True,
    )
    stage_id: Mapped[int] = mapped_column(
        ForeignKey("org.parameters.id", deferrable=True, initially="IMMEDIATE")
    )
    # "order" is a SQL reserved word — map the column name explicitly.
    order: Mapped[int] = mapped_column("order")
    is_initial: Mapped[bool] = mapped_column(default=False, server_default="false")
    is_final_positive: Mapped[bool] = mapped_column(default=False)


class ProfileTemplate(Base, AuditMixin, SoftDeleteMixin):
    """org.profile_templates — a universal reusable requirements profile for a role."""

    __tablename__ = "profile_templates"
    __table_args__ = {"schema": "org"}

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))


class ProfileTemplateItem(Base, AuditMixin, SoftDeleteMixin):
    """org.profile_template_items — a tagged requirement inside a template.

    `category_id` points at org.parameters (type=template_item_category:
    knowledge | tools | skills | certifications).
    """

    __tablename__ = "profile_template_items"
    __table_args__ = {"schema": "org"}

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    template_id: Mapped[int] = mapped_column(
        ForeignKey("org.profile_templates.id", deferrable=True, initially="IMMEDIATE"),
        index=True,
    )
    category_id: Mapped[int] = mapped_column(
        ForeignKey("org.parameters.id", deferrable=True, initially="IMMEDIATE")
    )
    name: Mapped[str] = mapped_column(String(300))
