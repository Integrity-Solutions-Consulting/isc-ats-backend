"""add case-insensitive unique index on auth.users lower(email)

Revision ID: b8c1a2d3e4f5
Revises: a66c0a65cb1d
Create Date: 2026-07-04

The existing uq_users_email constraint is case-sensitive, so "Foo@x.com" and
"foo@x.com" could both be stored — but login and the create-user duplicate check
normalize to lower(email), which would then match two rows (or the wrong one).
A functional unique index on lower(email) enforces the same normalization at the
DB level, closing the race between the application-level check and the insert.
"""

from alembic import op

revision = "b8c1a2d3e4f5"
down_revision = "a66c0a65cb1d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX uq_users_email_lower "
        "ON auth.users (lower(email))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS auth.uq_users_email_lower")
