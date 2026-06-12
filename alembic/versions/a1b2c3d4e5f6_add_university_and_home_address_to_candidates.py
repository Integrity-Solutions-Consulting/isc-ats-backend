"""add university_id and home_address to candidates; seed university catalog

Revision ID: a1b2c3d4e5f6
Revises: c4f9a2e3b701
Create Date: 2026-06-11

Adds two new nullable columns to recruitment.candidates:
  - university_id  FK -> org.parameters (type='university')
  - home_address   plain text, max 300 chars

Also seeds the `university` catalog with Ecuadorian universities.
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "c4f9a2e3b701"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# University catalog seed data
# ---------------------------------------------------------------------------

UNIVERSITIES = [
    ("epn", "Escuela Politécnica Nacional (EPN)"),
    ("espol", "Escuela Superior Politécnica del Litoral (ESPOL)"),
    ("usfq", "Universidad San Francisco de Quito (USFQ)"),
    ("puce", "Pontificia Universidad Católica del Ecuador (PUCE)"),
    ("uce", "Universidad Central del Ecuador (UCE)"),
    ("espe", "Universidad de las Fuerzas Armadas — ESPE"),
    ("utpl", "Universidad Técnica Particular de Loja (UTPL)"),
    ("ug", "Universidad de Guayaquil"),
    ("ucg", "Universidad Católica de Santiago de Guayaquil"),
    ("udla", "Universidad de las Américas (UDLA)"),
    ("uide", "Universidad Internacional del Ecuador (UIDE)"),
    ("ute", "Universidad Tecnológica Equinoccial (UTE)"),
    ("ucuenca", "Universidad de Cuenca"),
    ("uazuay", "Universidad del Azuay"),
    ("unemi", "Universidad Estatal de Milagro (UNEMI)"),
    ("uta", "Universidad Técnica de Ambato (UTA)"),
    ("utmachala", "Universidad Técnica de Machala"),
    ("pucese", "Pontificia Universidad Católica del Ecuador sede Esmeraldas"),
    ("flacso", "FLACSO Ecuador"),
    ("iaen", "Instituto de Altos Estudios Nacionales (IAEN)"),
    ("otra", "Otra universidad"),
]


def upgrade() -> None:
    # 1. Add new columns to recruitment.candidates
    op.add_column(
        "candidates",
        sa.Column("university_id", sa.Integer(), nullable=True),
        schema="recruitment",
    )
    op.add_column(
        "candidates",
        sa.Column("home_address", sa.String(length=300), nullable=True),
        schema="recruitment",
    )
    op.create_foreign_key(
        "fk_candidates_university_id_parameters",
        "candidates",
        "parameters",
        ["university_id"],
        ["id"],
        source_schema="recruitment",
        referent_schema="org",
        deferrable=True,
        initially="IMMEDIATE",
    )

    # 2. Seed the university catalog (idempotent)
    values_sql = ", ".join(
        f"('university', '{code}', '{name}', true, now())"
        for code, name in UNIVERSITIES
    )
    op.execute(
        f"""
        INSERT INTO org.parameters (type, code, name, is_active, created_at)
        VALUES {values_sql}
        ON CONFLICT (type, code) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_candidates_university_id_parameters",
        "candidates",
        schema="recruitment",
        type_="foreignkey",
    )
    op.drop_column("candidates", "home_address", schema="recruitment")
    op.drop_column("candidates", "university_id", schema="recruitment")

    op.execute(
        "DELETE FROM org.parameters WHERE type = 'university'"
    )
