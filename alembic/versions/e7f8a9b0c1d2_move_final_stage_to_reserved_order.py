"""move final process stage to a reserved high order

Revision ID: e7f8a9b0c1d2
Revises: b8c1a2d3e4f5
Create Date: 2026-07-06 00:00:00.000000

The final backbone stage (Contratados, is_final_positive) was auto-seeded at
order=2 and is protected (immovable). That pinned order 2, so no custom middle
stage could take it — breaking process creation/editing with intermediate
stages. This moves every existing final stage to the reserved order 9999 so it
always sorts last and orders 2..N are free for custom stages.

Downgrade moves it back to order 2 (best-effort; may clash if a custom stage was
since placed at order 2).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e7f8a9b0c1d2"
down_revision: str | None = "b8c1a2d3e4f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FINAL_STAGE_ORDER = 9999


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE org.process_stages
        SET "order" = {FINAL_STAGE_ORDER}
        WHERE is_final_positive = true AND "order" <> {FINAL_STAGE_ORDER}
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        UPDATE org.process_stages
        SET "order" = 2
        WHERE is_final_positive = true AND "order" = {FINAL_STAGE_ORDER}
        """
    )
