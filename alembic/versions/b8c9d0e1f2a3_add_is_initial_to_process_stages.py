"""add is_initial to process_stages, seed applicants param, rename offer param

Revision ID: b8c9d0e1f2a3
Revises: a4b5c6d7e8f9
Create Date: 2026-06-25 00:00:00.000000

Changes:
- ADD COLUMN is_initial BOOLEAN NOT NULL DEFAULT false to org.process_stages
- Seed org.parameters: (stage, applicants, 'Postulantes') ON CONFLICT DO NOTHING
- Rename (stage, offer) name from 'Oferta · Contratación' to 'Contratación'
- Backfill: for each active process that lacks an active 'applicants' stage,
  shift existing active stage orders +1 and insert Postulantes at order=1,
  is_initial=true. Uses offset dance to avoid partial-unique-index violations
  (indexes are non-deferrable WHERE is_active=TRUE).
- Guard is idempotent: re-running the DO $$ block inserts nothing if already applied.

Downgrade:
- Drops is_initial column.
- Reverts offer name back to 'Oferta · Contratación'.
- Does NOT reverse the backfill (no safe rollback for data insertion).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b8c9d0e1f2a3"
down_revision: str | None = "a4b5c6d7e8f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add column
    op.execute(
        """
        ALTER TABLE org.process_stages
        ADD COLUMN IF NOT EXISTS is_initial BOOLEAN NOT NULL DEFAULT false
        """
    )

    # 2. Seed 'applicants' stage parameter
    op.execute(
        """
        INSERT INTO org.parameters (type, code, name, is_active, created_at)
        VALUES ('stage', 'applicants', 'Postulantes', true, now())
        ON CONFLICT (type, code) DO NOTHING
        """
    )

    # 3. Rename 'offer' parameter
    op.execute(
        """
        UPDATE org.parameters
        SET name = 'Contratación'
        WHERE type = 'stage' AND code = 'offer'
        """
    )

    # 4. Backfill: insert Postulantes as the first (order=1, is_initial=true) stage
    #    for every active process that does not already have an active 'applicants' stage.
    #
    #    The partial unique index on (process_id, "order") WHERE is_active=TRUE is
    #    NON-DEFERRABLE, so we cannot just bump order in a single pass. We use the
    #    offset dance:
    #      Phase 1: shift existing active orders UP by +1,000,000 (clear slot 1)
    #      Phase 2: shift them DOWN by -999,999 (lands at original+1)
    #      Phase 3: insert the new Postulantes row at order=1
    #
    #    A guard subquery ensures idempotency: the block is skipped entirely for
    #    processes that already have an active 'applicants' stage.
    op.execute(
        """
        DO $$
        DECLARE
            applicants_id INTEGER;
            proc_id       INTEGER;
        BEGIN
            -- Resolve the applicants param id (must exist after step 2 above)
            SELECT id INTO applicants_id
            FROM org.parameters
            WHERE type = 'stage' AND code = 'applicants' AND is_active = TRUE;

            IF applicants_id IS NULL THEN
                RAISE EXCEPTION 'applicants stage parameter not found';
            END IF;

            -- Iterate over each active process that lacks an active applicants stage
            FOR proc_id IN
                SELECT p.id
                FROM org.processes p
                WHERE p.is_active = TRUE
                  AND NOT EXISTS (
                      SELECT 1
                      FROM org.process_stages ps
                      WHERE ps.process_id = p.id
                        AND ps.stage_id   = applicants_id
                        AND ps.is_active  = TRUE
                  )
            LOOP
                -- Phase 1: shift all active stage orders up by 1,000,000
                UPDATE org.process_stages
                SET "order" = "order" + 1000000
                WHERE process_id = proc_id
                  AND is_active  = TRUE;

                -- Phase 2: shift back down by 999,999 (net: original + 1)
                UPDATE org.process_stages
                SET "order" = "order" - 999999
                WHERE process_id = proc_id
                  AND is_active  = TRUE;

                -- Phase 3: insert Postulantes at order=1
                INSERT INTO org.process_stages
                    (process_id, stage_id, "order", is_initial, is_final_positive,
                     is_active, created_at)
                VALUES
                    (proc_id, applicants_id, 1, TRUE, FALSE, TRUE, now());
            END LOOP;
        END;
        $$
        """
    )


def downgrade() -> None:
    # Revert offer name
    op.execute(
        """
        UPDATE org.parameters
        SET name = 'Oferta · Contratación'
        WHERE type = 'stage' AND code = 'offer'
        """
    )

    # Drop column
    op.execute(
        "ALTER TABLE org.process_stages DROP COLUMN IF EXISTS is_initial"
    )

    # NOTE: We do NOT reverse the backfill (the inserted Postulantes stages remain)
    # and we do NOT delete the applicants parameter — those are considered safe to keep.
