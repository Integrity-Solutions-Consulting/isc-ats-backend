"""seed offer stage parameter required for process editor

The process editor always appends a fixed 'Oferta · Contratación' stage
(type=final) when saving. The route handler looks it up by name in
org.parameters with type='stage'. Without this row the lookup fails and
no stages are persisted — only the process header (name, client, dept).

The catalog hides this code via hiddenCodes: ['offer'] so it never
appears in the palette for manual addition.

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-06-15 00:00:00.000000

"""
from collections.abc import Sequence

from alembic import op


revision: str = "c2d3e4f5a6b7"
down_revision: str | None = "b1c2d3e4f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO org.parameters (type, code, name, is_active, created_at)
        VALUES ('stage', 'offer', 'Oferta · Contratación', true, now())
        ON CONFLICT (type, code) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM org.parameters WHERE type = 'stage' AND code = 'offer'"
    )
