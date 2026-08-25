"""add years_of_experience to candidates

Candidate-portal profile field (like phone/current_company), not an
application-time field. Nullable, additive column — no data migration.
Existing candidate rows must not be backfilled with an invented figure, same
philosophy already used for applications.salary_expectation. Supports
decimals (e.g. 0.5, 1.5) deliberately: whole-year granularity isn't required,
and a separate "months" field was rejected as unnecessary complexity.

Revision ID: 83c8956bfa26
Revises: 99c299cb8503
Create Date: 2026-08-25 02:09:41.693526

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '83c8956bfa26'
down_revision: str | None = '99c299cb8503'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "candidates",
        sa.Column("years_of_experience", sa.Numeric(precision=4, scale=1), nullable=True),
        schema="recruitment",
    )


def downgrade() -> None:
    op.drop_column("candidates", "years_of_experience", schema="recruitment")
