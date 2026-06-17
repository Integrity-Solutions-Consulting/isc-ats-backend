"""add degree_title to recruitment.candidates

Revision ID: b2c3d4e5f6a7
Revises: e5f6a7b8c9d0
Create Date: 2026-06-16

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "candidates",
        sa.Column("degree_title", sa.String(200), nullable=True),
        schema="recruitment",
    )


def downgrade() -> None:
    op.drop_column("candidates", "degree_title", schema="recruitment")
