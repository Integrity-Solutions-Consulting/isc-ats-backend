"""create the 7 bounded-context schemas

Alembic autogenerate does NOT emit CREATE SCHEMA, so this base migration creates
them explicitly. Every table migration that follows targets one of these schemas.

Revision ID: 0001_create_schemas
Revises:
Create Date: 2026-06-05

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_create_schemas"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMAS = ["auth", "org", "recruitment", "talent", "comms", "storage", "ai"]


def upgrade() -> None:
    for schema in SCHEMAS:
        op.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')


def downgrade() -> None:
    for schema in reversed(SCHEMAS):
        op.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
