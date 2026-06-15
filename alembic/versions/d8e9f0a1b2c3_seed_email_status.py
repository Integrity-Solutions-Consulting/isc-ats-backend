"""seed email_status parameters

Revision ID: d8e9f0a1b2c3
Revises: f1e2d3c4b5a6
Create Date: 2026-06-13 00:00:00.000000

"""
from collections.abc import Sequence

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "d8e9f0a1b2c3"
down_revision: str | None = "f1e2d3c4b5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO org.parameters (type, code, name, is_active, created_at)
        VALUES
            ('email_status', 'sent', 'Enviado', true, now()),
            ('email_status', 'failed', 'Error', true, now())
        ON CONFLICT (type, code) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM org.parameters "
        "WHERE type = 'email_status' AND code IN ('sent', 'failed')"
    )
