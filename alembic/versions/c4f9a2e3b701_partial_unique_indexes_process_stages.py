"""make process_stages unique indexes partial (active rows only)

Revision ID: c4f9a2e3b701
Revises: b7d4e1f09c23
Create Date: 2026-06-06

Soft-deleted process stages were blocking re-adds of the same stage type.
Partial indexes ensure the uniqueness constraints only apply to active rows.
"""

from alembic import op

revision = "c4f9a2e3b701"
down_revision = "b7d4e1f09c23"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('ALTER TABLE org.process_stages DROP CONSTRAINT IF EXISTS uq_process_stages_process_id_order')
    op.execute('ALTER TABLE org.process_stages DROP CONSTRAINT IF EXISTS uq_process_stages_process_id_stage_id')
    op.execute(
        'CREATE UNIQUE INDEX uq_process_stages_process_id_order '
        'ON org.process_stages (process_id, "order") WHERE is_active = TRUE'
    )
    op.execute(
        'CREATE UNIQUE INDEX uq_process_stages_process_id_stage_id '
        'ON org.process_stages (process_id, stage_id) WHERE is_active = TRUE'
    )


def downgrade() -> None:
    op.execute('DROP INDEX IF EXISTS org.uq_process_stages_process_id_order')
    op.execute('DROP INDEX IF EXISTS org.uq_process_stages_process_id_stage_id')
    op.execute(
        'ALTER TABLE org.process_stages ADD CONSTRAINT uq_process_stages_process_id_order '
        'UNIQUE (process_id, "order")'
    )
    op.execute(
        'ALTER TABLE org.process_stages ADD CONSTRAINT uq_process_stages_process_id_stage_id '
        'UNIQUE (process_id, stage_id)'
    )
