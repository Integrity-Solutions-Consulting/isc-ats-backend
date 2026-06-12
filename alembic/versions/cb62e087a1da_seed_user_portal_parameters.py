"""seed user_portal parameters

Revision ID: cb62e087a1da
Revises: b3e457000aae
Create Date: 2026-06-05 11:39:22.409995

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cb62e087a1da'
down_revision: str | None = 'b3e457000aae'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Structural seed data: code branches on these (portal routing / authz).
    # Idempotent so re-running against a partially-seeded DB is safe.
    op.execute(
        """
        INSERT INTO org.parameters (type, code, name, is_active, created_at)
        VALUES
            ('user_portal', 'staff', 'Staff', true, now()),
            ('user_portal', 'candidate', 'Candidato', true, now())
        ON CONFLICT (type, code) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM org.parameters "
        "WHERE type = 'user_portal' AND code IN ('staff', 'candidate')"
    )
