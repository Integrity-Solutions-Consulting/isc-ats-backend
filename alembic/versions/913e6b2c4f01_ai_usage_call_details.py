"""Record outcome and context for Gemini calls.

Revision ID: 913e6b2c4f01
Revises: 6d29f3a81c02
"""

from alembic import op
import sqlalchemy as sa

revision = "913e6b2c4f01"
down_revision = "6d29f3a81c02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in (
        sa.Column("status", sa.String(20), nullable=True),
        sa.Column("error_code", sa.String(40), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("task_id", sa.Integer(), nullable=True),
        sa.Column("entity_id", sa.Integer(), nullable=True),
    ):
        op.add_column("ai_usage_logs", column, schema="ai")


def downgrade() -> None:
    for name in ("entity_id", "task_id", "duration_ms", "error_code", "status"):
        op.drop_column("ai_usage_logs", name, schema="ai")
