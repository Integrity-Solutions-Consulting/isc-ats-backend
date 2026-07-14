"""rename draft vacancy_status to solicitud and make process_id nullable

Revision ID: a1c2e3f4b5d6
Revises: e7f8a9b0c1d2
Create Date: 2026-07-09 00:00:00.000000

Slice 2 of the internal-roles-vacancy-requests change (Option A — in-place rename):

1. Renames the 'draft' vacancy status to 'solicitud' (code='solicitud', name='Solicitud').
   A single UPDATE keeps the row's primary key (status_id FK) intact on all existing
   vacancies — no cascade updates needed. After upgrade(), 0 rows have code='draft'.

2. Makes recruitment.vacancies.process_id nullable (DROP NOT NULL). Internal-role
   vacancies can be created without being tied to a client process at draft time;
   the process link is supplied at publish (Slice 3).

Downgrade:
- Reverses solicitud → draft (code='draft', name='Borrador').
- Re-adds NOT NULL on process_id. Note: if any vacancy has process_id=NULL at
  downgrade time, the ALTER COLUMN will fail. Ensure all vacancies have a
  process_id set before running downgrade in production.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1c2e3f4b5d6"
down_revision: str | None = "e7f8a9b0c1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Rename draft → solicitud (in-place; FKs on status_id stay intact).
    op.execute(
        """
        UPDATE org.parameters
        SET code = 'solicitud', name = 'Solicitud'
        WHERE type = 'vacancy_status' AND code = 'draft'
        """
    )

    # 2. Make process_id nullable on recruitment.vacancies.
    op.alter_column(
        "vacancies",
        "process_id",
        existing_type=sa.Integer(),
        nullable=True,
        schema="recruitment",
    )


def downgrade() -> None:
    # 2. Re-add NOT NULL on process_id.
    #    Fails if any row has process_id=NULL — caller must fix data first.
    op.alter_column(
        "vacancies",
        "process_id",
        existing_type=sa.Integer(),
        nullable=False,
        schema="recruitment",
    )

    # 1. Restore draft from solicitud (code='draft', name='Borrador').
    op.execute(
        """
        UPDATE org.parameters
        SET code = 'draft', name = 'Borrador'
        WHERE type = 'vacancy_status' AND code = 'solicitud'
        """
    )
