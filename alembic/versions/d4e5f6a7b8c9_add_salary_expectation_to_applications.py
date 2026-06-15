"""add salary_expectation to recruitment.applications

Column exists in the Application model but was omitted from the original
create_table migration. Without it the pipeline query crashes with
UndefinedColumnError, making the Kanban view return empty for all vacancies.

Revision ID: d4e5f6a7b8c9
Revises: c2d3e4f5a6b7
Create Date: 2026-06-15 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "c2d3e4f5a6b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "applications",
        sa.Column("salary_expectation", sa.Numeric(precision=10, scale=2), nullable=True),
        schema="recruitment",
    )


def downgrade() -> None:
    op.drop_column("applications", "salary_expectation", schema="recruitment")
