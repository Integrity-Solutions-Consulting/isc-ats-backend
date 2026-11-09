"""Persist AI work independently of Redis and Gemini."""

import sqlalchemy as sa
from alembic import op

revision = "6d29f3a81c02"
down_revision = "83c8956bfa26"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("file_id", sa.Integer(), sa.ForeignKey("storage.files.id"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_token", sa.String(36)),
        sa.Column("error_code", sa.String(40)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("action", "entity_id", "file_id"),
        schema="ai",
    )
    op.create_index("ix_ai_tasks_next_attempt_at", "tasks", ["next_attempt_at"], schema="ai")
    # Recover previously uploaded CVs which have no successful extraction.
    op.execute("""
        INSERT INTO ai.tasks
          (action, entity_id, file_id, status, attempts, next_attempt_at, updated_at)
        SELECT 'parse_candidate_cv', id, cv_file_id, 'pending', 0, now(), now()
        FROM recruitment.candidates
        WHERE is_active AND cv_file_id IS NOT NULL AND parsed_data IS NULL
    """)
    op.execute("""
        INSERT INTO ai.tasks
          (action, entity_id, file_id, status, attempts, next_attempt_at, updated_at)
        SELECT 'analyze_application', a.id, c.cv_file_id, 'pending', 0, now(), now()
        FROM recruitment.applications a
        JOIN recruitment.candidates c ON c.id = a.candidate_id
        WHERE a.is_active AND c.is_active AND c.cv_file_id IS NOT NULL
          AND a.match_score IS NULL
    """)


def downgrade() -> None:
    op.drop_table("tasks", schema="ai")
