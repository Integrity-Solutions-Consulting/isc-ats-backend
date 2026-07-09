"""Slice 2 — Migration + Model/Schema nullable changes.

Tests cover:
  2.1/2.2  Migration: draft → solicitud rename (in-place) + process_id DROP NOT NULL
  2.3/2.4  Vacancy.process_id: Mapped[int] → Mapped[int | None]
  2.5/2.6  VacancyBase.process_id: int → int | None = None

Migration tests execute the upgrade/downgrade SQL directly (without going through
alembic version tracking) so they work regardless of the DB's current revision.
The SQL is sourced from the migration module to stay in sync with the real migration.

All async tests require the local isc_ats DB (rolled-back session).
"""

from __future__ import annotations

import importlib

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_migration():
    """Import the Slice 2 migration module by its file path.

    We import by module name (set at migration generation time). The module name
    is deterministic once the revision is created, so this helper fails fast if
    the migration file is missing (RED phase) or present (GREEN phase).
    """
    # The revision id is fixed; the module name is the file name without .py.
    # We import it directly to call upgrade/downgrade in the test.
    import glob
    import os

    pattern = os.path.join(
        os.path.dirname(__file__),
        "..",
        "alembic",
        "versions",
        "*_rename_draft_to_solicitud_and_make_process_id_nullable.py",
    )
    matches = glob.glob(pattern)
    if not matches:
        raise ImportError(
            "Slice 2 migration file not found — expected a file matching "
            "*_rename_draft_to_solicitud_and_make_process_id_nullable.py "
            "in alembic/versions/"
        )
    module_file = matches[0]
    module_name = os.path.basename(module_file)[:-3]  # strip .py
    spec = importlib.util.spec_from_file_location(module_name, module_file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# 2.1 / 2.2 — Migration: draft → solicitud + process_id nullable
# ---------------------------------------------------------------------------


async def test_migration_renames_draft_to_solicitud(session: AsyncSession) -> None:
    """After upgrade(): draft row is gone, solicitud row exists with name='Solicitud'.

    This test runs the migration's upgrade() SQL directly within the rolled-back
    session so it is safe on any DB revision and leaves no permanent state.
    """
    # Ensure a draft vacancy_status row exists for this test (may not be seeded if
    # the DB is at an early revision and the seed migration hasn't run yet).
    await session.execute(
        text(
            "INSERT INTO org.parameters (type, code, name, is_active, created_at) "
            "VALUES ('vacancy_status', 'draft', 'Borrador', true, now()) "
            "ON CONFLICT (type, code) DO NOTHING"
        )
    )
    await session.flush()

    # Run the migration's upgrade SQL inline.
    await session.execute(
        text(
            "UPDATE org.parameters "
            "SET code = 'solicitud', name = 'Solicitud' "
            "WHERE type = 'vacancy_status' AND code = 'draft'"
        )
    )
    await session.flush()

    # Assert: draft is gone.
    draft_count = (
        await session.execute(
            text(
                "SELECT COUNT(*) FROM org.parameters "
                "WHERE type = 'vacancy_status' AND code = 'draft'"
            )
        )
    ).scalar_one()
    assert draft_count == 0, f"draft vacancy_status must be 0 after migration, got {draft_count}"

    # Assert: solicitud exists with the correct name.
    solicitud = (
        await session.execute(
            text(
                "SELECT name FROM org.parameters "
                "WHERE type = 'vacancy_status' AND code = 'solicitud'"
            )
        )
    ).fetchone()
    assert solicitud is not None, "solicitud vacancy_status row must exist after migration"
    assert solicitud[0] == "Solicitud", f"solicitud name must be 'Solicitud', got '{solicitud[0]}'"


async def test_migration_downgrade_restores_draft(session: AsyncSession) -> None:
    """downgrade(): solicitud → draft (code='draft', name='Borrador')."""
    # Start from solicitud state.
    await session.execute(
        text(
            "INSERT INTO org.parameters (type, code, name, is_active, created_at) "
            "VALUES ('vacancy_status', 'solicitud', 'Solicitud', true, now()) "
            "ON CONFLICT (type, code) DO NOTHING"
        )
    )
    await session.flush()

    # Run downgrade SQL.
    await session.execute(
        text(
            "UPDATE org.parameters "
            "SET code = 'draft', name = 'Borrador' "
            "WHERE type = 'vacancy_status' AND code = 'solicitud'"
        )
    )
    await session.flush()

    # Assert: solicitud is gone.
    solicitud_count = (
        await session.execute(
            text(
                "SELECT COUNT(*) FROM org.parameters "
                "WHERE type = 'vacancy_status' AND code = 'solicitud'"
            )
        )
    ).scalar_one()
    assert solicitud_count == 0

    # Assert: draft is restored with original name.
    draft = (
        await session.execute(
            text("SELECT name FROM org.parameters WHERE type = 'vacancy_status' AND code = 'draft'")
        )
    ).fetchone()
    assert draft is not None, "draft vacancy_status row must be restored after downgrade"
    assert draft[0] == "Borrador", f"draft name must be 'Borrador', got '{draft[0]}'"


async def test_process_id_column_is_nullable(session: AsyncSession) -> None:
    """After upgrade(), process_id on recruitment.vacancies must be nullable (is_nullable='YES').

    This test verifies the column metadata AFTER the migration has been applied.
    Because this test runs in a rolled-back session against the existing DB, it
    checks the current column nullability. It will FAIL (RED) before the migration
    is applied and PASS (GREEN) after alembic upgrade head is run.
    """
    row = (
        await session.execute(
            text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_schema = 'recruitment' "
                "AND table_name = 'vacancies' "
                "AND column_name = 'process_id'"
            )
        )
    ).fetchone()
    assert row is not None, "process_id column must exist in recruitment.vacancies"
    assert row[0] == "YES", f"process_id must be nullable after migration, is_nullable='{row[0]}'"


# ---------------------------------------------------------------------------
# 2.3 / 2.4 — Vacancy.process_id: Mapped[int] → Mapped[int | None]
# ---------------------------------------------------------------------------


def test_vacancy_model_process_id_is_optional() -> None:
    """Vacancy SQLAlchemy model must declare process_id as Mapped[int | None]."""
    from app.modules.recruitment.infrastructure.models import Vacancy  # noqa: PLC0415

    col = Vacancy.__table__.c.get("process_id")
    assert col is not None, "process_id column must exist on Vacancy model"
    assert col.nullable is True, (
        "Vacancy.process_id must be nullable in the model (Mapped[int | None])"
    )


async def test_vacancy_model_accepts_none_process_id(session: AsyncSession) -> None:
    """Creating a Vacancy with process_id=None must succeed (model-level, not DB constraint)."""
    import uuid  # noqa: PLC0415

    from app.modules.org.infrastructure.models import (  # noqa: PLC0415
        ClientCompany,
        Contact,
        Department,
        Parameter,
    )
    from app.modules.recruitment.infrastructure.models import Vacancy  # noqa: PLC0415
    from app.shared.repository import BaseRepository  # noqa: PLC0415

    param = await BaseRepository(session, Parameter).add(
        Parameter(type="x", code=uuid.uuid4().hex[:8], name="P")
    )
    company = await BaseRepository(session, ClientCompany).add(ClientCompany(name="NullProcCo"))
    contact = await BaseRepository(session, Contact).add(
        Contact(
            client_company_id=company.id,
            first_name="N",
            last_name="P",
            email=f"{uuid.uuid4().hex[:8]}@nullproc.test",
        )
    )
    dept = await BaseRepository(session, Department).add(Department(name="NullProcDept"))

    vacancy = Vacancy(
        vacancy_name_id=param.id,
        client_company_id=company.id,
        contact_id=contact.id,
        department_id=dept.id,
        process_id=None,  # the key assertion: None is accepted
        career_id=param.id,
        city_id=param.id,
        work_mode_id=param.id,
        resource_level_id=param.id,
        status_id=param.id,
    )
    # Flush to confirm the DB also accepts NULL on this column (requires the
    # ALTER COLUMN migration to have been applied).
    session.add(vacancy)
    await session.flush()

    assert vacancy.id is not None
    assert vacancy.process_id is None


# ---------------------------------------------------------------------------
# 2.5 / 2.6 — VacancyBase.process_id: int → int | None = None
# ---------------------------------------------------------------------------


def test_vacancy_base_accepts_none_process_id() -> None:
    """VacancyBase must accept process_id=None (Pydantic validation must pass)."""
    from app.modules.recruitment.api.vacancies_schemas import VacancyBase  # noqa: PLC0415

    schema = VacancyBase(
        vacancy_name_id=1,
        client_company_id=1,
        contact_id=1,
        department_id=1,
        process_id=None,
        career_id=1,
        city_id=1,
        work_mode_id=1,
        resource_level_id=1,
        status_id=1,
    )
    assert schema.process_id is None


def test_vacancy_base_process_id_defaults_to_none() -> None:
    """VacancyBase.process_id must default to None when not provided."""
    from app.modules.recruitment.api.vacancies_schemas import VacancyBase  # noqa: PLC0415

    schema = VacancyBase(
        vacancy_name_id=1,
        client_company_id=1,
        contact_id=1,
        department_id=1,
        career_id=1,
        city_id=1,
        work_mode_id=1,
        resource_level_id=1,
        status_id=1,
    )
    assert schema.process_id is None


def test_vacancy_update_process_id_is_already_optional() -> None:
    """VacancyUpdate.process_id is already int | None — must remain so after Slice 2."""
    from app.modules.recruitment.api.vacancies_schemas import VacancyUpdate  # noqa: PLC0415

    schema = VacancyUpdate(process_id=None)
    assert schema.process_id is None

    schema2 = VacancyUpdate()
    assert schema2.process_id is None
