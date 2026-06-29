"""add rejected_at_stage_id to applications

Revision ID: c5d6e7f8a9b0
Revises: a9f1c7e3b2d8
Create Date: 2026-06-29 00:00:00.000000

Adds a nullable FK recording the process stage a candidate had reached at the
moment of rejection. Rejection nulls current_stage_id, so without this column the
"how far did I get" information is lost. Read-only context for the candidate UI.

Downgrade drops the column.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c5d6e7f8a9b0"
down_revision: str | None = "a9f1c7e3b2d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE recruitment.applications
        ADD COLUMN IF NOT EXISTS rejected_at_stage_id INTEGER
            REFERENCES org.process_stages(id)
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE recruitment.applications DROP COLUMN IF EXISTS rejected_at_stage_id"
    )
