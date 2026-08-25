"""add_auth_consents

Revision ID: cac9a09107f0
Revises: 2bbdb59a6b75
Create Date: 2026-08-20 14:18:34.694156

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cac9a09107f0'
down_revision: str | None = '2bbdb59a6b75'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('consents',
    sa.Column('id', sa.Integer(), sa.Identity(always=False), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('consent_type', sa.String(length=32), nullable=False),
    sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_source', sa.String(length=32), nullable=True),
    sa.Column('policy_version', sa.String(length=32), nullable=False),
    sa.Column('ip_address', sa.String(length=45), nullable=True),
    sa.Column('source', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('ip_created', sa.String(length=45), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_by', sa.Integer(), nullable=True),
    sa.Column('ip_updated', sa.String(length=45), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['auth.users.id'], name=op.f('fk_consents_user_id_users'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_consents')),
    schema='auth'
    )
    # Partial unique index: at most one ACTIVE (non-revoked) consent row per
    # user+type. Revoked rows (including refusals, which are revoked from
    # insertion) are excluded, so history can accumulate freely.
    op.execute(
        "CREATE UNIQUE INDEX uq_consents_user_id_consent_type_active "
        "ON auth.consents (user_id, consent_type) WHERE revoked_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX auth.uq_consents_user_id_consent_type_active")
    op.drop_table('consents', schema='auth')
