"""drop client_company_id and department_id from profile_templates

Revision ID: b7d4e1f09c23
Revises: 3f8a2c91d047
Create Date: 2026-06-06

Templates are universal — no client or department restriction.
"""

from alembic import op
import sqlalchemy as sa

revision = "b7d4e1f09c23"
down_revision = "3f8a2c91d047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "fk_profile_templates_client_company_id_client_companies",
        "profile_templates",
        schema="org",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_profile_templates_department_id_departments",
        "profile_templates",
        schema="org",
        type_="foreignkey",
    )
    op.drop_column("profile_templates", "client_company_id", schema="org")
    op.drop_column("profile_templates", "department_id", schema="org")


def downgrade() -> None:
    op.add_column(
        "profile_templates",
        sa.Column("department_id", sa.Integer(), nullable=True),
        schema="org",
    )
    op.add_column(
        "profile_templates",
        sa.Column("client_company_id", sa.Integer(), nullable=True),
        schema="org",
    )
