"""drop province from candidates, add title catalog + title_id

Removes the `province` concept system-wide: a city already determines its
province unambiguously, and the client-facing document only needs city, so
province was redundant and unvalidatable (no FK between city/province in the
flat org.parameters catalog).

- drops recruitment.candidates.province_id (and its FK)
- removes the org.parameters rows of type 'province'
- adds recruitment.candidates.title_id (FK org.parameters)
- seeds the 'title' catalog (degree obtained: Ingeniero, Tecnólogo, ...)
- seeds a starter 'career' catalog (study field: Software, Derecho, ...)

`degree_title` (free text) is left in place but deprecated — the title is now a
normalised catalog reference via title_id.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-06-18 00:00:00.000000

"""
from collections.abc import Sequence

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Drop province_id (Postgres drops the dependent FK constraint with it).
    op.execute("ALTER TABLE recruitment.candidates DROP COLUMN IF EXISTS province_id")

    # 2. Remove the province catalog.
    op.execute("DELETE FROM org.parameters WHERE type = 'province'")

    # 3. Add title_id with the same deferrable FK style as the other refs.
    op.execute("ALTER TABLE recruitment.candidates ADD COLUMN IF NOT EXISTS title_id INTEGER")
    op.execute(
        """
        ALTER TABLE recruitment.candidates
        ADD CONSTRAINT candidates_title_id_fkey
        FOREIGN KEY (title_id) REFERENCES org.parameters(id)
        DEFERRABLE INITIALLY IMMEDIATE
        """
    )

    # 4. Seed the title catalog (degree obtained) + a starter career catalog.
    op.execute(
        """
        INSERT INTO org.parameters (type, code, name, is_active, created_at)
        VALUES
            -- Title / degree obtained (short, closed set).
            ('title', 'bachiller',  'Bachiller',        true, now()),
            ('title', 'tecnico',    'Técnico Superior', true, now()),
            ('title', 'tecnologo',  'Tecnólogo',        true, now()),
            ('title', 'ingeniero',  'Ingeniero',        true, now()),
            ('title', 'licenciado', 'Licenciado',       true, now()),
            ('title', 'economista', 'Economista',       true, now()),
            ('title', 'abogado',    'Abogado',          true, now()),
            ('title', 'medico',     'Médico',           true, now()),
            ('title', 'arquitecto', 'Arquitecto',       true, now()),
            ('title', 'magister',   'Magíster',         true, now()),
            ('title', 'doctor',     'Doctor (PhD)',     true, now()),

            -- Starter career catalog (study field). Filled out for real later.
            ('career', 'software',       'Software',                       true, now()),
            ('career', 'sistemas',       'Sistemas / Computación',         true, now()),
            ('career', 'electronica',    'Electrónica / Telecomunicaciones', true, now()),
            ('career', 'industrial',     'Industrial',                     true, now()),
            ('career', 'administracion', 'Administración',                 true, now()),
            ('career', 'contabilidad',   'Contabilidad / Auditoría',       true, now()),
            ('career', 'economia',       'Economía / Finanzas',            true, now()),
            ('career', 'marketing',      'Marketing',                      true, now()),
            ('career', 'comunicacion',   'Comunicación',                   true, now()),
            ('career', 'derecho',        'Derecho',                        true, now()),
            ('career', 'psicologia',     'Psicología',                     true, now())
        ON CONFLICT (type, code) DO NOTHING
        """
    )


def downgrade() -> None:
    # Restore the province_id column (data is not recovered).
    op.execute("ALTER TABLE recruitment.candidates ADD COLUMN IF NOT EXISTS province_id INTEGER")
    op.execute(
        """
        ALTER TABLE recruitment.candidates
        ADD CONSTRAINT candidates_province_id_fkey
        FOREIGN KEY (province_id) REFERENCES org.parameters(id)
        DEFERRABLE INITIALLY IMMEDIATE
        """
    )
    op.execute("ALTER TABLE recruitment.candidates DROP CONSTRAINT IF EXISTS candidates_title_id_fkey")
    op.execute("ALTER TABLE recruitment.candidates DROP COLUMN IF EXISTS title_id")
    op.execute("DELETE FROM org.parameters WHERE type = 'title'")
