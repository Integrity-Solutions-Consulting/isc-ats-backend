"""seed structural system parameters required in production

Seeds the org.parameters rows that application code branches on by `code`
(vacancy_status, application_status, ai_job_status, notification_channel,
stage_status) plus the national reference catalogs the candidate flow needs
(education_level, province). These previously lived only in scripts/seed_dev_data.py
(dev-only), so a production deploy that runs `alembic upgrade head` was missing
them and core flows (publish vacancy, register candidate) could not work.

Business/demo data (client companies, cities, careers, vacancy names, etc.)
stays in scripts/seed_dev_data.py — it is not structural.

Revision ID: a7c3f1e9d2b4
Revises: f2b3c4d5e6f7
Create Date: 2026-06-15 00:00:00.000000

"""
from collections.abc import Sequence

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "a7c3f1e9d2b4"
down_revision: str | None = "f2b3c4d5e6f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SEEDED_TYPES: tuple[str, ...] = (
    "vacancy_status",
    "application_status",
    "ai_job_status",
    "notification_channel",
    "stage_status",
    "education_level",
    "province",
)


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO org.parameters (type, code, name, is_active, created_at)
        VALUES
            -- Vacancy lifecycle. Code branches on 'active' (vacancies_routes).
            ('vacancy_status', 'active', 'Activa',   true, now()),
            ('vacancy_status', 'draft',  'Borrador', true, now()),
            ('vacancy_status', 'paused', 'Pausada',  true, now()),
            ('vacancy_status', 'closed', 'Cerrada',  true, now()),

            -- Global application outcome.
            ('application_status', 'active',    'Activa',    true, now()),
            ('application_status', 'rejected',  'Rechazada', true, now()),
            ('application_status', 'withdrawn', 'Retirada',  true, now()),
            ('application_status', 'hired',     'Contratada', true, now()),

            -- CV parsing job lifecycle.
            ('ai_job_status', 'pending',    'Pendiente',   true, now()),
            ('ai_job_status', 'processing', 'Procesando',  true, now()),
            ('ai_job_status', 'completed',  'Completado',  true, now()),
            ('ai_job_status', 'failed',     'Error',       true, now()),

            -- Notification delivery channels.
            ('notification_channel', 'in_app', 'En la aplicación', true, now()),
            ('notification_channel', 'email',  'Correo',           true, now()),

            -- Kanban sub-states within a stage. Editable defaults — HR may
            -- adjust these per their real pipeline sub-statuses.
            ('stage_status', 'in_review',     'En revisión',            true, now()),
            ('stage_status', 'waiting_client', 'Espera respuesta cliente', true, now()),
            ('stage_status', 'advanced',      'Avanzado',               true, now()),
            ('stage_status', 'on_hold',       'En pausa',               true, now()),

            -- Highest completed education level (candidate onboarding).
            ('education_level', 'secundaria',   'Secundaria / Bachillerato', true, now()),
            ('education_level', 'tecnico',      'Técnico / Tecnólogo',       true, now()),
            ('education_level', 'tercer_nivel', 'Tercer nivel / Pregrado',   true, now()),
            ('education_level', 'cuarto_nivel', 'Cuarto nivel / Postgrado',  true, now()),
            ('education_level', 'doctorado',    'Doctorado / PhD',           true, now()),

            -- Provinces of Ecuador (24, official).
            ('province', 'azuay',           'Azuay',                          true, now()),
            ('province', 'bolivar',         'Bolívar',                        true, now()),
            ('province', 'canar',           'Cañar',                          true, now()),
            ('province', 'carchi',          'Carchi',                         true, now()),
            ('province', 'chimborazo',      'Chimborazo',                     true, now()),
            ('province', 'cotopaxi',        'Cotopaxi',                       true, now()),
            ('province', 'el_oro',          'El Oro',                         true, now()),
            ('province', 'esmeraldas',      'Esmeraldas',                     true, now()),
            ('province', 'galapagos',       'Galápagos',                      true, now()),
            ('province', 'guayas',          'Guayas',                         true, now()),
            ('province', 'imbabura',        'Imbabura',                       true, now()),
            ('province', 'loja',            'Loja',                           true, now()),
            ('province', 'los_rios',        'Los Ríos',                       true, now()),
            ('province', 'manabi',          'Manabí',                         true, now()),
            ('province', 'morona_santiago', 'Morona Santiago',                true, now()),
            ('province', 'napo',            'Napo',                           true, now()),
            ('province', 'orellana',        'Orellana',                       true, now()),
            ('province', 'pastaza',         'Pastaza',                        true, now()),
            ('province', 'pichincha',       'Pichincha',                      true, now()),
            ('province', 'santa_elena',     'Santa Elena',                    true, now()),
            ('province', 'santo_domingo',   'Santo Domingo de los Tsáchilas', true, now()),
            ('province', 'sucumbios',       'Sucumbíos',                      true, now()),
            ('province', 'tungurahua',      'Tungurahua',                     true, now()),
            ('province', 'zamora_chinchipe', 'Zamora Chinchipe',              true, now())
        ON CONFLICT (type, code) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM org.parameters WHERE type IN ("
        "'vacancy_status', 'application_status', 'ai_job_status', "
        "'notification_channel', 'stage_status', 'education_level', 'province')"
    )
