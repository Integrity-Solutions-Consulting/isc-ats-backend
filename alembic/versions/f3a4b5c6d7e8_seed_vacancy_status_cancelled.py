"""seed vacancy_status 'cancelled'

Adds the 'cancelled' (Cancelada) vacancy status. The lifecycle distinguishes a
successful close ('closed' — requires all openings filled) from an abandoned one
('cancelled' — allowed at any time). Code branches on these codes in
vacancies_service, so the row must exist in every environment.

Revision ID: f3a4b5c6d7e8
Revises: c3d4e5f6a7b8
Create Date: 2026-06-19 00:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3a4b5c6d7e8"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO org.parameters (type, code, name, is_active, created_at)
        VALUES ('vacancy_status', 'cancelled', 'Cancelada', true, now())
        ON CONFLICT (type, code) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM org.parameters
        WHERE type = 'vacancy_status' AND code = 'cancelled'
        """
    )
