"""rename offer stage parameter from 'Contratación' to 'Contratados'

The terminal-positive stage label is aligned with the other two fixed stages
(Postulantes / Rechazados): all three read as reached states, not activities.
'Contratación' suggested an in-progress activity; 'Contratados' makes it clear
the candidate has already won the position.

Only the visible name changes. The code stays 'offer' (the stable key the
backbone-stage lookups rely on), so process creation/sync is unaffected.

Revision ID: a9f1c7e3b2d8
Revises: b8c9d0e1f2a3
Create Date: 2026-06-28 00:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

revision: str = "a9f1c7e3b2d8"
down_revision: str | None = "b8c9d0e1f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE org.parameters
        SET name = 'Contratados'
        WHERE type = 'stage' AND code = 'offer'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE org.parameters
        SET name = 'Contratación'
        WHERE type = 'stage' AND code = 'offer'
        """
    )
