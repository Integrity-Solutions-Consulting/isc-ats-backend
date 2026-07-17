"""add external_id to org.client_companies

Revision ID: 7c2e9a4f1b83
Revises: b4c5d6e7f8a9
Create Date: 2026-07-17 00:00:00.000000

Adds org.client_companies.external_id — the TMR (external .NET system) client id
that a locally-mirrored row was sourced from. Nullable so legacy/local rows keep
working, and covered by a PARTIAL unique index (WHERE external_id IS NOT NULL) so
every TMR-sourced row maps to exactly one external id while any number of purely
local rows may leave it NULL. Clients are mirrored on a sync-on-read basis from
TMR's REST API and upserted keyed on this column.

Downgrade drops the index and the column; any recorded external ids are lost.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7c2e9a4f1b83"
down_revision: str | None = "b4c5d6e7f8a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "client_companies",
        sa.Column("external_id", sa.Integer(), nullable=True),
        schema="org",
    )
    # Plain lookup index (mirrors index=True on the model column).
    op.create_index(
        op.f("ix_org_client_companies_external_id"),
        "client_companies",
        ["external_id"],
        unique=False,
        schema="org",
    )
    # Partial unique index: one row per TMR id, but multiple local rows may keep
    # external_id NULL. The multiple-NULLs semantics is why this lives here and
    # not as unique=True on the model column.
    op.create_index(
        "uq_org_client_companies_external_id",
        "client_companies",
        ["external_id"],
        unique=True,
        schema="org",
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_org_client_companies_external_id",
        table_name="client_companies",
        schema="org",
    )
    op.drop_index(
        op.f("ix_org_client_companies_external_id"),
        table_name="client_companies",
        schema="org",
    )
    op.drop_column("client_companies", "external_id", schema="org")
