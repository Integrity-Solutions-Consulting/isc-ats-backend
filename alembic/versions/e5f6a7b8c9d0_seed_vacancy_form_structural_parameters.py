"""seed structural parameters required by the vacancy form

work_mode, resource_level and city were only in scripts/seed_dev_data.py.
Without them a fresh production deploy cannot create vacancies:
  - work_mode / resource_level: the route handler looks them up by code and
    raises an error if not found, aborting the POST/PATCH.
  - city: the vacancy form combo is empty so the required field cannot be filled.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-06-15 00:00:00.000000

"""
from collections.abc import Sequence

from alembic import op


revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO org.parameters (type, code, name, is_active, created_at)
        VALUES
            -- Work modes (codes are referenced directly by the vacancy route handler)
            ('work_mode', 'onsite',  'Presencial',    true, now()),
            ('work_mode', 'hybrid',  'Híbrido',       true, now()),
            ('work_mode', 'remote',  'Remoto',        true, now()),
            ('work_mode', 'project', 'Por proyecto',  true, now()),

            -- Resource levels (codes are referenced directly by the vacancy route handler)
            ('resource_level', 'junior',      'Junior',       true, now()),
            ('resource_level', 'semi_senior', 'Semi Senior',  true, now()),
            ('resource_level', 'senior',      'Senior',       true, now()),
            ('resource_level', 'specialist',  'Especialista', true, now()),

            -- Main Ecuador cities (minimum set for the vacancy form to be usable)
            ('city', 'guayaquil',    'Guayaquil',    true, now()),
            ('city', 'quito',        'Quito',        true, now()),
            ('city', 'cuenca',       'Cuenca',       true, now()),
            ('city', 'ambato',       'Ambato',       true, now()),
            ('city', 'manta',        'Manta',        true, now()),
            ('city', 'machala',      'Machala',      true, now()),
            ('city', 'santo_domingo','Santo Domingo', true, now()),
            ('city', 'riobamba',     'Riobamba',     true, now()),
            ('city', 'ibarra',       'Ibarra',       true, now()),
            ('city', 'loja',         'Loja',         true, now()),
            ('city', 'esmeraldas',   'Esmeraldas',   true, now()),
            ('city', 'portoviejo',   'Portoviejo',   true, now()),
            ('city', 'duran',        'Durán',        true, now()),
            ('city', 'samborondon',  'Samborondón',  true, now()),
            ('city', 'milagro',      'Milagro',      true, now()),
            ('city', 'quevedo',      'Quevedo',      true, now()),
            ('city', 'latacunga',    'Latacunga',    true, now()),
            ('city', 'babahoyo',     'Babahoyo',     true, now()),
            ('city', 'salinas',      'Salinas',      true, now())
        ON CONFLICT (type, code) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM org.parameters WHERE type IN ('work_mode', 'resource_level', 'city')"
    )
