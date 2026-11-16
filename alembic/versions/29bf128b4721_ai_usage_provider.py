"""Identify the provider used by each AI attempt.

Revision ID: 29bf128b4721
Revises: 913e6b2c4f01
"""
import sqlalchemy as sa

from alembic import op

revision = "29bf128b4721"
down_revision = "913e6b2c4f01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ai_usage_logs", sa.Column("provider", sa.String(40), nullable=True), schema="ai")


def downgrade() -> None:
    op.drop_column("ai_usage_logs", "provider", schema="ai")
