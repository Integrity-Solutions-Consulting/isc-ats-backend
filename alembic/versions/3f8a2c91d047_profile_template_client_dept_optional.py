"""make profile_template client_company and department optional

Revision ID: 3f8a2c91d047
Revises: 57987bca8afc
Create Date: 2026-06-06

Templates are now universal — not tied to a specific client or department.
"""

from alembic import op
import sqlalchemy as sa

revision = "3f8a2c91d047"
down_revision = "57987bca8afc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "profile_templates",
        "client_company_id",
        existing_type=sa.Integer(),
        nullable=True,
        schema="org",
    )
    op.alter_column(
        "profile_templates",
        "department_id",
        existing_type=sa.Integer(),
        nullable=True,
        schema="org",
    )


def downgrade() -> None:
    # department_id was already nullable — no change needed
    op.alter_column(
        "profile_templates",
        "client_company_id",
        existing_type=sa.Integer(),
        nullable=False,
        schema="org",
    )
