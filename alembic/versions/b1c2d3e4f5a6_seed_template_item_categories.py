"""seed template_item_category parameters required for profile templates

The four category codes (knowledge, tools, skills, certifications) are used
by the route handler POST /org/profile-templates to resolve category_id when
creating profile-template-items. Without them, the handler silently skips
all items and only the template name is persisted.

These were previously only in scripts/seed_dev_data.py (dev-only). Moving
them here ensures a clean production deploy via `alembic upgrade head` has
all structural parameters required for the profile-templates feature.

Revision ID: b1c2d3e4f5a6
Revises: a7c3f1e9d2b4
Create Date: 2026-06-15 00:00:00.000000

"""
from collections.abc import Sequence

from alembic import op


revision: str = "b1c2d3e4f5a6"
down_revision: str | None = "a7c3f1e9d2b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO org.parameters (type, code, name, is_active, created_at)
        VALUES
            ('template_item_category', 'knowledge',      'Conocimientos',  true, now()),
            ('template_item_category', 'tools',          'Herramientas',   true, now()),
            ('template_item_category', 'skills',         'Habilidades',    true, now()),
            ('template_item_category', 'certifications', 'Certificaciones', true, now())
        ON CONFLICT (type, code) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM org.parameters WHERE type = 'template_item_category'"
    )
