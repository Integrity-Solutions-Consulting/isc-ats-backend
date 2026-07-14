"""Slice 1 — Catalog + Bootstrap + SYSTEM_ROLE_NAMES.

Tests cover:
  1.1  recruitment.vacancies.publish exists in PERMISSION_CATALOG
  1.2  grant_permissions_to_role helper (generalized from grant_candidate_permissions_to_role)
  1.3  TALENTO_HUMANO_PERMISSION_CODES allowlist + bootstrap wiring
  1.4  COMERCIAL_PERMISSION_CODES / PROYECTO_PERMISSION_CODES wiring
  1.5  SYSTEM_ROLE_NAMES contains the three new internal roles + rename/delete protection

All async tests require the local isc_ats DB migrated to head (rolled-back session).
"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.permissions_catalog import ALL_CODES, PERMISSION_CATALOG

# ---------------------------------------------------------------------------
# 1.1 — catalog includes recruitment.vacancies.publish  (RED → GREEN)
# ---------------------------------------------------------------------------


def test_catalog_includes_vacancies_publish() -> None:
    """recruitment.vacancies.publish must be present in PERMISSION_CATALOG."""
    codes = {spec.code for spec in PERMISSION_CATALOG}
    assert "recruitment.vacancies.publish" in codes


def test_vacancies_publish_is_in_all_codes() -> None:
    """ALL_CODES frozenset must include recruitment.vacancies.publish."""
    assert "recruitment.vacancies.publish" in ALL_CODES


def test_vacancies_publish_spec_is_well_formed() -> None:
    """The PermissionSpec for publish must have module='recruitment' and a non-empty name."""
    spec = next((s for s in PERMISSION_CATALOG if s.code == "recruitment.vacancies.publish"), None)
    assert spec is not None
    assert spec.module == "recruitment"
    assert spec.name


# ---------------------------------------------------------------------------
# 1.2 — grant_permissions_to_role helper  (RED → GREEN)
# ---------------------------------------------------------------------------


def test_grant_permissions_to_role_is_importable() -> None:
    """The generalized helper must be importable from bootstrap_service."""
    from app.modules.auth.application.bootstrap_service import (
        grant_permissions_to_role,  # noqa: PLC0415
    )

    assert callable(grant_permissions_to_role)


async def test_grant_permissions_to_role_upserts_active(session: AsyncSession) -> None:
    """grant_permissions_to_role upserts active grants for every code in the allowlist."""
    from app.modules.auth.application.bootstrap_service import (  # noqa: PLC0415
        grant_permissions_to_role,
        sync_permissions,
    )
    from app.modules.auth.infrastructure.models import (  # noqa: PLC0415
        Permission,
        Role,
        RolePermission,
    )

    # Permissions table must be populated before we can grant by code.
    await sync_permissions(session)

    role = Role(name=f"TestHelper-{uuid.uuid4().hex[:8]}", description="test")
    session.add(role)
    await session.flush()

    allowlist = frozenset({"org.parameters.read"})
    grants = await grant_permissions_to_role(session, role.id, allowlist)
    assert grants == 1

    pid_row = (
        await session.execute(select(Permission.id).where(Permission.code == "org.parameters.read"))
    ).scalar_one_or_none()
    assert pid_row is not None

    rp = (
        await session.execute(
            select(RolePermission).where(
                RolePermission.role_id == role.id,
                RolePermission.permission_id == pid_row,
                RolePermission.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    assert rp is not None


async def test_grant_permissions_to_role_revokes_outside_allowlist(
    session: AsyncSession,
) -> None:
    """grant_permissions_to_role must revoke permissions NOT in the allowlist."""
    from app.modules.auth.application.bootstrap_service import (  # noqa: PLC0415
        grant_permissions_to_role,
        sync_permissions,
    )
    from app.modules.auth.infrastructure.models import (  # noqa: PLC0415
        Permission,
        Role,
        RolePermission,
    )

    await sync_permissions(session)

    role = Role(name=f"TestRevoke-{uuid.uuid4().hex[:8]}", description="test")
    session.add(role)
    await session.flush()

    # Grant two codes first, then tighten allowlist to one.
    broad_allowlist = frozenset({"org.parameters.read", "org.departments.read"})
    await grant_permissions_to_role(session, role.id, broad_allowlist)

    narrow_allowlist = frozenset({"org.parameters.read"})
    await grant_permissions_to_role(session, role.id, narrow_allowlist)

    # org.departments.read must now be revoked (is_active=False).
    dept_pid = (
        await session.execute(
            select(Permission.id).where(Permission.code == "org.departments.read")
        )
    ).scalar_one()

    rp = (
        await session.execute(
            select(RolePermission).where(
                RolePermission.role_id == role.id,
                RolePermission.permission_id == dept_pid,
            )
        )
    ).scalar_one_or_none()
    assert rp is not None
    assert rp.is_active is False


async def test_grant_permissions_to_role_idempotent(session: AsyncSession) -> None:
    """Calling grant_permissions_to_role twice must not duplicate rows or change state."""
    from app.modules.auth.application.bootstrap_service import (  # noqa: PLC0415
        grant_permissions_to_role,
        sync_permissions,
    )
    from app.modules.auth.infrastructure.models import (  # noqa: PLC0415
        Permission,
        Role,
        RolePermission,
    )

    await sync_permissions(session)

    role = Role(name=f"TestIdem-{uuid.uuid4().hex[:8]}", description="test")
    session.add(role)
    await session.flush()

    allowlist = frozenset({"org.parameters.read", "storage.files.read"})
    await grant_permissions_to_role(session, role.id, allowlist)
    await grant_permissions_to_role(session, role.id, allowlist)

    # Must still have exactly 2 active grants — no duplicates.
    pids = (
        (await session.execute(select(Permission.id).where(Permission.code.in_(allowlist))))
        .scalars()
        .all()
    )

    rps = (
        (
            await session.execute(
                select(RolePermission).where(
                    RolePermission.role_id == role.id,
                    RolePermission.is_active.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rps) == len(allowlist)
    assert {r.permission_id for r in rps} == set(pids)


# ---------------------------------------------------------------------------
# 1.3 — TALENTO_HUMANO_PERMISSION_CODES allowlist + bootstrap wiring  (RED → GREEN)
# ---------------------------------------------------------------------------


def test_talento_humano_permission_codes_importable() -> None:
    from app.modules.auth.application.bootstrap_service import (  # noqa: PLC0415
        TALENTO_HUMANO_PERMISSION_CODES,
    )

    assert isinstance(TALENTO_HUMANO_PERMISSION_CODES, frozenset)
    assert len(TALENTO_HUMANO_PERMISSION_CODES) > 0


def test_talento_humano_codes_exist_in_catalog() -> None:
    """Every code in TALENTO_HUMANO_PERMISSION_CODES must exist in ALL_CODES."""
    from app.modules.auth.application.bootstrap_service import (  # noqa: PLC0415
        TALENTO_HUMANO_PERMISSION_CODES,
    )

    missing = TALENTO_HUMANO_PERMISSION_CODES - ALL_CODES
    assert not missing, f"Codes not in catalog: {missing}"


def test_talento_humano_permission_codes_exact_set() -> None:
    """TALENTO_HUMANO_PERMISSION_CODES must match the authoritative allowlist from design #388."""
    from app.modules.auth.application.bootstrap_service import (  # noqa: PLC0415
        TALENTO_HUMANO_PERMISSION_CODES,
    )

    expected = frozenset(
        {
            # recruitment — full pipeline management (update/publish, not create)
            "recruitment.vacancies.read",
            "recruitment.vacancies.update",
            "recruitment.vacancies.delete",
            "recruitment.vacancies.publish",
            "recruitment.candidates.read",
            "recruitment.candidates.create",
            "recruitment.candidates.update",
            "recruitment.candidates.delete",
            "recruitment.applications.read",
            "recruitment.applications.create",
            "recruitment.applications.update",
            "recruitment.applications.delete",
            "recruitment.application_notes.read",
            "recruitment.application_notes.create",
            "recruitment.application_notes.update",
            "recruitment.application_notes.delete",
            "recruitment.application_documents.read",
            "recruitment.interviews.read",
            "recruitment.interviews.create",
            "recruitment.interviews.update",
            "recruitment.interviews.delete",
            # org — processes; parameters read+create+update; read-only on
            # departments/client_companies/contacts
            "org.departments.read",
            "org.client_companies.read",
            "org.processes.read",
            "org.processes.create",
            "org.processes.update",
            "org.processes.delete",
            "org.process_stages.read",
            "org.process_stages.create",
            "org.process_stages.update",
            "org.process_stages.delete",
            "org.contacts.read",
            "org.parameters.read",
            "org.parameters.create",
            "org.parameters.update",
            "org.parameters.delete",
            # talent
            "talent.talent_pool.read",
            "talent.talent_pool.create",
            "talent.talent_pool.delete",
            # storage
            "storage.files.read",
            "storage.files.create",
            # comms
            "comms.notifications.read",
        }
    )
    assert TALENTO_HUMANO_PERMISSION_CODES == expected


def test_talento_humano_departments_and_client_companies_read_only() -> None:
    """TH must have org.departments/org.client_companies READ only — no write access.

    TH needs these to populate the cliente/departamento filters on the vacancies
    list, but does not manage either catalog (no create/update/delete).
    """
    from app.modules.auth.application.bootstrap_service import (  # noqa: PLC0415
        TALENTO_HUMANO_PERMISSION_CODES,
    )

    scoped = {
        c
        for c in TALENTO_HUMANO_PERMISSION_CODES
        if "departments" in c or "client_companies" in c
    }
    assert scoped == {"org.departments.read", "org.client_companies.read"}, (
        f"TH must hold exactly departments/client_companies READ, nothing else: {scoped}"
    )


def test_talento_humano_no_ai_permissions() -> None:
    """TH must NOT have any ai.* permissions per design #388."""
    from app.modules.auth.application.bootstrap_service import (  # noqa: PLC0415
        TALENTO_HUMANO_PERMISSION_CODES,
    )

    ai_codes = {c for c in TALENTO_HUMANO_PERMISSION_CODES if c.startswith("ai.")}
    assert not ai_codes, f"TH must not hold ai.* permissions: {ai_codes}"


def test_talento_humano_no_profile_template_permissions() -> None:
    """TH must NOT hold any org.profile_templates.* or org.profile_template_items.*
    permission (not even read) — profile templates are owned exclusively by
    Comercial/Proyecto per product decision."""
    from app.modules.auth.application.bootstrap_service import (  # noqa: PLC0415
        TALENTO_HUMANO_PERMISSION_CODES,
    )

    forbidden = {
        c
        for c in TALENTO_HUMANO_PERMISSION_CODES
        if "profile_templates" in c or "profile_template_items" in c
    }
    assert not forbidden, f"TH must not hold any profile_template permissions: {forbidden}"


async def test_bootstrap_creates_talento_humano_role_with_correct_grants(
    session: AsyncSession,
) -> None:
    """After bootstrap, Talento Humano exists with exactly TALENTO_HUMANO_PERMISSION_CODES."""
    from app.modules.auth.application.bootstrap_service import (  # noqa: PLC0415
        TALENTO_HUMANO_PERMISSION_CODES,
        bootstrap_admin,
    )
    from app.modules.auth.infrastructure.models import (  # noqa: PLC0415
        Permission,
        Role,
        RolePermission,
    )

    await bootstrap_admin(session, f"admin-{uuid.uuid4().hex[:10]}@test.local", "S3cret")

    role = (
        await session.execute(
            select(Role).where(Role.name == "Talento Humano").where(Role.is_active.is_(True))
        )
    ).scalar_one_or_none()
    assert role is not None, "Talento Humano role must exist after bootstrap"

    granted_codes = set(
        (
            await session.execute(
                select(Permission.code)
                .join(RolePermission, RolePermission.permission_id == Permission.id)
                .where(RolePermission.role_id == role.id)
                .where(RolePermission.is_active.is_(True))
            )
        )
        .scalars()
        .all()
    )
    assert granted_codes == set(TALENTO_HUMANO_PERMISSION_CODES)


async def test_bootstrap_talento_humano_is_idempotent(session: AsyncSession) -> None:
    """Running bootstrap twice must not duplicate or drop Talento Humano grants."""
    from app.modules.auth.application.bootstrap_service import (  # noqa: PLC0415
        TALENTO_HUMANO_PERMISSION_CODES,
        bootstrap_admin,
    )
    from app.modules.auth.infrastructure.models import (  # noqa: PLC0415
        Permission,
        Role,
        RolePermission,
    )

    email = f"admin-{uuid.uuid4().hex[:10]}@test.local"
    await bootstrap_admin(session, email, "S3cret")
    await bootstrap_admin(session, email, "S3cret")

    role = (
        await session.execute(
            select(Role).where(Role.name == "Talento Humano").where(Role.is_active.is_(True))
        )
    ).scalar_one_or_none()
    assert role is not None

    granted_codes = set(
        (
            await session.execute(
                select(Permission.code)
                .join(RolePermission, RolePermission.permission_id == Permission.id)
                .where(RolePermission.role_id == role.id)
                .where(RolePermission.is_active.is_(True))
            )
        )
        .scalars()
        .all()
    )
    assert granted_codes == set(TALENTO_HUMANO_PERMISSION_CODES)


# ---------------------------------------------------------------------------
# 1.4 — COMERCIAL_PERMISSION_CODES / PROYECTO_PERMISSION_CODES  (RED → GREEN)
# ---------------------------------------------------------------------------


def test_comercial_permission_codes_importable() -> None:
    from app.modules.auth.application.bootstrap_service import (  # noqa: PLC0415
        COMERCIAL_PERMISSION_CODES,
    )

    assert isinstance(COMERCIAL_PERMISSION_CODES, frozenset)


def test_proyecto_permission_codes_importable() -> None:
    from app.modules.auth.application.bootstrap_service import (  # noqa: PLC0415
        PROYECTO_PERMISSION_CODES,
    )

    assert isinstance(PROYECTO_PERMISSION_CODES, frozenset)


def test_comercial_equals_proyecto() -> None:
    """COMERCIAL and PROYECTO must be identical allowlists."""
    from app.modules.auth.application.bootstrap_service import (  # noqa: PLC0415
        COMERCIAL_PERMISSION_CODES,
        PROYECTO_PERMISSION_CODES,
    )

    assert COMERCIAL_PERMISSION_CODES == PROYECTO_PERMISSION_CODES


def test_comercial_codes_exist_in_catalog() -> None:
    from app.modules.auth.application.bootstrap_service import (  # noqa: PLC0415
        COMERCIAL_PERMISSION_CODES,
    )

    missing = COMERCIAL_PERMISSION_CODES - ALL_CODES
    assert not missing, f"Codes not in catalog: {missing}"


def test_comercial_permission_codes_exact_set() -> None:
    """COMERCIAL_PERMISSION_CODES must match the authoritative allowlist from design #388."""
    from app.modules.auth.application.bootstrap_service import (  # noqa: PLC0415
        COMERCIAL_PERMISSION_CODES,
    )

    expected = frozenset(
        {
            # recruitment — vacancy read+create only; pipeline read-only
            "recruitment.vacancies.read",
            "recruitment.vacancies.create",
            "recruitment.candidates.read",
            "recruitment.applications.read",
            "recruitment.application_notes.read",
            "recruitment.application_documents.read",
            "recruitment.interviews.read",
            # org — contacts read; profile templates/items full CRUD; parameters read+create+update
            "org.contacts.read",
            "org.profile_templates.read",
            "org.profile_templates.create",
            "org.profile_templates.update",
            "org.profile_templates.delete",
            "org.profile_template_items.read",
            "org.profile_template_items.create",
            "org.profile_template_items.update",
            "org.profile_template_items.delete",
            "org.parameters.read",
            "org.parameters.create",
            "org.parameters.update",
            # talent — read only
            "talent.talent_pool.read",
            # storage
            "storage.files.read",
            "storage.files.create",
            # comms
            "comms.notifications.read",
        }
    )
    assert COMERCIAL_PERMISSION_CODES == expected


def test_comercial_no_vacancy_publish_update_delete() -> None:
    """Comercial must NOT have vacancy publish, update, or delete — TH owns lifecycle."""
    from app.modules.auth.application.bootstrap_service import (  # noqa: PLC0415
        COMERCIAL_PERMISSION_CODES,
    )

    forbidden = {
        "recruitment.vacancies.publish",
        "recruitment.vacancies.update",
        "recruitment.vacancies.delete",
    }
    violations = forbidden & COMERCIAL_PERMISSION_CODES
    assert not violations, f"Comercial must not hold: {violations}"


def test_comercial_no_processes_or_process_stages() -> None:
    """Comercial must NOT have org.processes.* or org.process_stages.* (TH-only)."""
    from app.modules.auth.application.bootstrap_service import (  # noqa: PLC0415
        COMERCIAL_PERMISSION_CODES,
    )

    forbidden = {c for c in COMERCIAL_PERMISSION_CODES if "processes" in c or "process_stages" in c}
    assert not forbidden, f"Comercial must not hold process/stage permissions: {forbidden}"


def test_comercial_no_ai_permissions() -> None:
    """Comercial must NOT have any ai.* permissions per design #388."""
    from app.modules.auth.application.bootstrap_service import (  # noqa: PLC0415
        COMERCIAL_PERMISSION_CODES,
    )

    ai_codes = {c for c in COMERCIAL_PERMISSION_CODES if c.startswith("ai.")}
    assert not ai_codes, f"Comercial must not hold ai.* permissions: {ai_codes}"


def test_comercial_pipeline_write_absent() -> None:
    """Comercial pipeline access is read-only: no write on applications, notes, or interviews."""
    from app.modules.auth.application.bootstrap_service import (  # noqa: PLC0415
        COMERCIAL_PERMISSION_CODES,
    )

    pipeline_write = {
        c
        for c in COMERCIAL_PERMISSION_CODES
        if any(res in c for res in ("applications", "application_notes", "interviews"))
        and not c.endswith(".read")
    }
    assert not pipeline_write, f"Comercial must not hold pipeline write codes: {pipeline_write}"


def test_comercial_talent_pool_read_only() -> None:
    """Comercial talent_pool access is read-only — no create or delete."""
    from app.modules.auth.application.bootstrap_service import (  # noqa: PLC0415
        COMERCIAL_PERMISSION_CODES,
    )

    forbidden = {
        "talent.talent_pool.create",
        "talent.talent_pool.delete",
    }
    violations = forbidden & COMERCIAL_PERMISSION_CODES
    assert not violations, f"Comercial must not hold talent pool write codes: {violations}"


async def test_bootstrap_creates_comercial_role_with_correct_grants(
    session: AsyncSession,
) -> None:
    """After bootstrap, Comercial exists with exactly COMERCIAL_PERMISSION_CODES."""
    from app.modules.auth.application.bootstrap_service import (  # noqa: PLC0415
        COMERCIAL_PERMISSION_CODES,
        bootstrap_admin,
    )
    from app.modules.auth.infrastructure.models import (  # noqa: PLC0415
        Permission,
        Role,
        RolePermission,
    )

    await bootstrap_admin(session, f"admin-{uuid.uuid4().hex[:10]}@test.local", "S3cret")

    role = (
        await session.execute(
            select(Role).where(Role.name == "Comercial").where(Role.is_active.is_(True))
        )
    ).scalar_one_or_none()
    assert role is not None, "Comercial role must exist after bootstrap"

    granted_codes = set(
        (
            await session.execute(
                select(Permission.code)
                .join(RolePermission, RolePermission.permission_id == Permission.id)
                .where(RolePermission.role_id == role.id)
                .where(RolePermission.is_active.is_(True))
            )
        )
        .scalars()
        .all()
    )
    assert granted_codes == set(COMERCIAL_PERMISSION_CODES)


async def test_bootstrap_creates_proyecto_role_with_correct_grants(
    session: AsyncSession,
) -> None:
    """After bootstrap, Proyecto exists with exactly PROYECTO_PERMISSION_CODES."""
    from app.modules.auth.application.bootstrap_service import (  # noqa: PLC0415
        PROYECTO_PERMISSION_CODES,
        bootstrap_admin,
    )
    from app.modules.auth.infrastructure.models import (  # noqa: PLC0415
        Permission,
        Role,
        RolePermission,
    )

    await bootstrap_admin(session, f"admin-{uuid.uuid4().hex[:10]}@test.local", "S3cret")

    role = (
        await session.execute(
            select(Role).where(Role.name == "Proyecto").where(Role.is_active.is_(True))
        )
    ).scalar_one_or_none()
    assert role is not None, "Proyecto role must exist after bootstrap"

    granted_codes = set(
        (
            await session.execute(
                select(Permission.code)
                .join(RolePermission, RolePermission.permission_id == Permission.id)
                .where(RolePermission.role_id == role.id)
                .where(RolePermission.is_active.is_(True))
            )
        )
        .scalars()
        .all()
    )
    assert granted_codes == set(PROYECTO_PERMISSION_CODES)


# ---------------------------------------------------------------------------
# 1.5 — SYSTEM_ROLE_NAMES + rename/delete protection  (RED → GREEN)
# ---------------------------------------------------------------------------


def test_system_role_names_includes_internal_roles() -> None:
    """SYSTEM_ROLE_NAMES must include the three internal roles."""
    from app.modules.auth.application.roles_service import SYSTEM_ROLE_NAMES  # noqa: PLC0415

    assert "Talento Humano" in SYSTEM_ROLE_NAMES
    assert "Comercial" in SYSTEM_ROLE_NAMES
    assert "Proyecto" in SYSTEM_ROLE_NAMES


async def test_delete_talento_humano_raises_system_error(session: AsyncSession) -> None:
    """Deleting 'Talento Humano' must raise SystemRoleError (→ 409 via HTTP)."""
    from app.modules.auth.application.bootstrap_service import bootstrap_admin  # noqa: PLC0415
    from app.modules.auth.application.roles_service import (  # noqa: PLC0415
        RoleService,
        SystemRoleError,
    )
    from app.modules.auth.infrastructure.models import Role  # noqa: PLC0415
    from app.shared.repository import BaseRepository  # noqa: PLC0415

    await bootstrap_admin(session, f"admin-{uuid.uuid4().hex[:10]}@test.local", "S3cret")

    role = (
        await session.execute(
            select(Role).where(Role.name == "Talento Humano").where(Role.is_active.is_(True))
        )
    ).scalar_one()

    service = RoleService(BaseRepository(session, Role))
    with pytest.raises(SystemRoleError):
        await service.delete(role.id)


async def test_rename_comercial_raises_system_error(session: AsyncSession) -> None:
    """Renaming 'Comercial' must raise SystemRoleError (→ 409 via HTTP)."""
    from app.core.dependencies import CurrentUser  # noqa: PLC0415
    from app.modules.auth.api.roles_schemas import RoleUpdate  # noqa: PLC0415
    from app.modules.auth.application.bootstrap_service import bootstrap_admin  # noqa: PLC0415
    from app.modules.auth.application.roles_service import (  # noqa: PLC0415
        RoleService,
        SystemRoleError,
    )
    from app.modules.auth.infrastructure.models import Role  # noqa: PLC0415
    from app.shared.repository import BaseRepository  # noqa: PLC0415

    await bootstrap_admin(session, f"admin-{uuid.uuid4().hex[:10]}@test.local", "S3cret")

    role = (
        await session.execute(
            select(Role).where(Role.name == "Comercial").where(Role.is_active.is_(True))
        )
    ).scalar_one()

    service = RoleService(BaseRepository(session, Role))
    actor = CurrentUser(user_id=1, ip="127.0.0.1")
    with pytest.raises(SystemRoleError):
        await service.update(role.id, RoleUpdate(name="Renamed"), actor)


async def test_delete_talento_humano_endpoint_409(session: AsyncSession) -> None:
    """DELETE /roles/{id} for 'Talento Humano' must return 409."""
    from collections.abc import AsyncGenerator  # noqa: PLC0415

    from app.core.database import get_session  # noqa: PLC0415
    from app.core.security import create_access_token  # noqa: PLC0415
    from app.main import app  # noqa: PLC0415
    from app.modules.auth.application.bootstrap_service import bootstrap_admin  # noqa: PLC0415
    from app.modules.auth.infrastructure.models import Role  # noqa: PLC0415

    async def _use_test_session() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_session] = _use_test_session
    try:
        admin = await bootstrap_admin(
            session, f"admin-{uuid.uuid4().hex[:10]}@test.local", "S3cret"
        )
        role = (
            await session.execute(
                select(Role).where(Role.name == "Talento Humano").where(Role.is_active.is_(True))
            )
        ).scalar_one()

        token = create_access_token(admin.user_id, extra_claims={"portal": "staff"})
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.delete(
                f"/api/v1/auth/roles/{role.id}",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert r.status_code == 409
        assert "system" in r.json()["detail"].lower()
    finally:
        app.dependency_overrides.clear()


async def test_rename_comercial_endpoint_409(session: AsyncSession) -> None:
    """PATCH /roles/{id} renaming 'Comercial' must return 409."""
    from collections.abc import AsyncGenerator  # noqa: PLC0415

    from app.core.database import get_session  # noqa: PLC0415
    from app.core.security import create_access_token  # noqa: PLC0415
    from app.main import app  # noqa: PLC0415
    from app.modules.auth.application.bootstrap_service import bootstrap_admin  # noqa: PLC0415
    from app.modules.auth.infrastructure.models import Role  # noqa: PLC0415

    async def _use_test_session() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_session] = _use_test_session
    try:
        admin = await bootstrap_admin(
            session, f"admin-{uuid.uuid4().hex[:10]}@test.local", "S3cret"
        )
        role = (
            await session.execute(
                select(Role).where(Role.name == "Comercial").where(Role.is_active.is_(True))
            )
        ).scalar_one()

        token = create_access_token(admin.user_id, extra_claims={"portal": "staff"})
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.patch(
                f"/api/v1/auth/roles/{role.id}",
                json={"name": "HackedComercial"},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert r.status_code == 409
        assert "system" in r.json()["detail"].lower()
    finally:
        app.dependency_overrides.clear()
