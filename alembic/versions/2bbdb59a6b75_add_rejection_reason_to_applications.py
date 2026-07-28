"""add rejection_reason to recruitment.applications

Revision ID: 2bbdb59a6b75
Revises: 7c2e9a4f1b83
Create Date: 2026-07-23 00:00:00.000000

Free-text reason shown to the candidate (email + in-app notification) when an
application is rejected — either typed by HR in the Kanban move, or the fixed
system copy used when the vacancy itself is closed/deleted. Nullable: existing
rows and any transition that predates this column simply have no reason on file.
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2bbdb59a6b75"
down_revision: str | None = "7c2e9a4f1b83"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "applications",
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        schema="recruitment",
    )


def downgrade() -> None:
    op.drop_column("applications", "rejection_reason", schema="recruitment")
