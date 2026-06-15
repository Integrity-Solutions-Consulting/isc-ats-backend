"""seed interview_status and interview_scheduler parameters

Revision ID: e1a2b3c4d5e6
Revises: d8e9f0a1b2c3
Create Date: 2026-06-14 00:00:00.000000

"""
from collections.abc import Sequence

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "e1a2b3c4d5e6"
down_revision: str | None = "d8e9f0a1b2c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO org.parameters (type, code, name, is_active, created_at)
        VALUES
            ('interview_status',    'scheduled', 'Agendada',   true, now()),
            ('interview_status',    'offered',   'Ofrecida',   true, now()),
            ('interview_status',    'confirmed',  'Confirmada', true, now()),
            ('interview_status',    'cancelled',  'Cancelada',  true, now()),
            ('interview_status',    'completed',  'Completada', true, now()),
            ('interview_scheduler', 'hr',         'RH',         true, now()),
            ('interview_scheduler', 'candidate',  'Candidato',  true, now())
        ON CONFLICT (type, code) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM org.parameters "
        "WHERE type IN ('interview_status', 'interview_scheduler')"
    )
