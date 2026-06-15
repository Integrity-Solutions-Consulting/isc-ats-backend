"""add buffer_min to interviewer_availability, token_expires_at + nullable schedule to interviews

Revision ID: f2b3c4d5e6f7
Revises: e1a2b3c4d5e6
Create Date: 2026-06-14 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "f2b3c4d5e6f7"
down_revision: str | None = "e1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # -- interviewer_availability.buffer_min ---------------------------------
    # Default 0 min; new rows get 0 automatically; existing rows too via server_default.
    op.add_column(
        "interviewer_availability",
        sa.Column(
            "buffer_min",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        schema="recruitment",
    )

    # -- interviews.token_expires_at -----------------------------------------
    op.add_column(
        "interviews",
        sa.Column(
            "token_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        schema="recruitment",
    )

    # -- interviews.scheduled_at / ends_at -> NULLABLE -----------------------
    op.alter_column(
        "interviews",
        "scheduled_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=True,
        schema="recruitment",
    )
    op.alter_column(
        "interviews",
        "ends_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=True,
        schema="recruitment",
    )


def downgrade() -> None:
    # Reverse order
    op.alter_column(
        "interviews",
        "ends_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
        schema="recruitment",
    )
    op.alter_column(
        "interviews",
        "scheduled_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
        schema="recruitment",
    )
    op.drop_column("interviews", "token_expires_at", schema="recruitment")
    op.drop_column("interviewer_availability", "buffer_min", schema="recruitment")
