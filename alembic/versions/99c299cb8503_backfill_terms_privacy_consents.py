"""backfill_terms_privacy_consents

Existing candidate-portal users never explicitly accepted terms/privacy
through the new auth.consents flow — they agreed at registration time, before
this table existed. This backfills one terms_privacy row per candidate user,
using their own auth.users.created_at as accepted_at (not now()), so the
recorded acceptance date reflects when they actually registered.

Marketing consent is untouched: zero marketing rows are created here, since
marketing opt-in is always an explicit, forward-looking action (Slice 2+).

Revision ID: 99c299cb8503
Revises: cac9a09107f0
Create Date: 2026-08-20 14:18:48.829830

"""
from collections.abc import Sequence

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '99c299cb8503'
down_revision: str | None = 'cac9a09107f0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO auth.consents (user_id, consent_type, accepted_at, policy_version, source, is_active, created_at)
        SELECT u.id, 'terms_privacy', u.created_at, 'pre-versioning', 'backfill', true, now()
        FROM auth.users u
        JOIN org.parameters p ON p.id = u.portal_id
        WHERE p.type = 'user_portal' AND p.code = 'candidate'
          AND NOT EXISTS (
              SELECT 1 FROM auth.consents c
              WHERE c.user_id = u.id AND c.consent_type = 'terms_privacy'
          )
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM auth.consents WHERE source = 'backfill'")
