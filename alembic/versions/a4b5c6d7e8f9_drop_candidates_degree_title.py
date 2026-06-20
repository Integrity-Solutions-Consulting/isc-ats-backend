"""drop deprecated candidates.degree_title

The free-text `degree_title` was superseded by the normalised `title_id` catalog
reference (migration c3d4e5f6a7b8). All readers now resolve the title from
title_id, so the column is dead and is dropped here.

Revision ID: a4b5c6d7e8f9
Revises: f3a4b5c6d7e8
Create Date: 2026-06-19 00:00:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "a4b5c6d7e8f9"
down_revision: str | None = "f3a4b5c6d7e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE recruitment.candidates DROP COLUMN IF EXISTS degree_title")


def downgrade() -> None:
    op.add_column(
        "candidates",
        sa.Column("degree_title", sa.String(length=200), nullable=True),
        schema="recruitment",
    )
