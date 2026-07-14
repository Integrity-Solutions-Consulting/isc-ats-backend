"""add role parameter type grants

Revision ID: b4c5d6e7f8a9
Revises: a1c2e3f4b5d6
Create Date: 2026-07-13 00:00:00.000000

Adds auth.role_parameter_type_grants — a per-role allowlist of org.parameters
catalog TYPES (org.parameters.type values, e.g. "vacancy_name", "stage",
"stage_status") that the role is allowed to create/update via
POST/PATCH /org/parameters. Replaces the previous hardcoded, global
"non-admins may only write vacancy_name" rule with a configurable,
per-role grant.

Mirrors auth.role_permissions exactly in shape (composite PK, AuditMixin +
SoftDeleteMixin columns, deferred FK on role_id) except the second half of the
key is a plain string (`parameter_type`), not a foreign key — org.parameters.type
is not backed by a modeled entity anywhere in the schema (Parameter.type is a
plain String column), so there is nothing to reference.

Downgrade drops the table; any grants recorded in it are lost.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b4c5d6e7f8a9"
down_revision: str | None = "a1c2e3f4b5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "role_parameter_type_grants",
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("parameter_type", sa.String(length=50), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("ip_created", sa.String(length=45), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("ip_updated", sa.String(length=45), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["auth.roles.id"],
            name=op.f("fk_role_parameter_type_grants_role_id_roles"),
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.PrimaryKeyConstraint(
            "role_id",
            "parameter_type",
            name=op.f("pk_role_parameter_type_grants"),
        ),
        schema="auth",
    )


def downgrade() -> None:
    op.drop_table("role_parameter_type_grants", schema="auth")
