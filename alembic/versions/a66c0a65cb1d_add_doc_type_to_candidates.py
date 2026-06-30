"""add doc_type to candidates

Revision ID: a66c0a65cb1d
Revises: c5d6e7f8a9b0
Create Date: 2026-06-30 00:55:35.401750

Adds the document type (cedula | passport) so the API can validate the cedula
column against the right rule instead of guessing by length. Existing rows are
backfilled to 'cedula' (the type the system previously assumed for everyone).

Downgrade drops the column.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a66c0a65cb1d"
down_revision: str | None = "c5d6e7f8a9b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE recruitment.candidates
        ADD COLUMN IF NOT EXISTS doc_type VARCHAR(20) NOT NULL DEFAULT 'cedula'
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE recruitment.candidates DROP COLUMN IF EXISTS doc_type"
    )
