"""Idempotent bootstrap of the RBAC baseline: permission catalog, admin role, admin user.

Runs the same way every time — re-invoking against an already-bootstrapped database
only refreshes permission metadata and leaves existing rows intact. Driven by the
permissions_catalog (source of truth), so no static seed data lives in migrations.
"""

from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.modules.auth.infrastructure.models import (
    Permission,
    Role,
    RoleParameterTypeGrant,
    RolePermission,
    User,
    UserRole,
)
from app.modules.auth.permissions_catalog import PERMISSION_CATALOG
from app.modules.org.infrastructure.parameters_repository import ParameterRepository

ADMIN_ROLE_NAME = "Administrador"
ADMIN_PORTAL_CODE = "staff"

CANDIDATE_ROLE_NAME = "candidate"

# Internal staff role name constants — single source of truth; imported by
# notification fan-out and any other code that resolves roles by name.
TALENTO_HUMANO_ROLE_NAME = "Talento Humano"
COMERCIAL_ROLE_NAME = "Comercial"
PROYECTO_ROLE_NAME = "Proyecto"

# Exact permission codes the candidate-portal BFF is allowed to call.
# Verified against the frontend API calls — do not expand without a front-end audit.
CANDIDATE_PERMISSION_CODES: frozenset[str] = frozenset(
    {
        # Narrow: stage names only. NOT the coarse recruitment.vacancies.read, which
        # also unlocks pipeline/documents/client info (cross-candidate PII leak).
        "recruitment.vacancies.read_stages",
        "recruitment.candidates.read",
        "recruitment.candidates.create",
        "recruitment.candidates.update",
        "recruitment.applications.read",
        "recruitment.applications.create",
        "storage.files.read",
        "storage.files.create",
        "org.parameters.read",
    }
)

# Internal staff role — Talento Humano.
# Full recruitment pipeline management (vacancies update + publish, full candidate/application
# pipeline, interviews). Owns process/stage configuration. Talent pool
# read+create+delete. Parameters read+create+update (a per-role type allowlist narrows
# create/update/delete to stage/stage_status — see auth.role_parameter_type_grants).
# Read-only on org.departments, org.client_companies and org.contacts — needed to
# populate filters and to see who to coordinate with, but TH does not manage those
# catalogs (no create/update/delete). No ai.* — outside TH's operational scope per
# design #388.
# recruitment.vacancies.create is intentionally NOT granted — TH never creates a new
# vacancy from scratch, it only updates/publishes an existing "solicitud"-status vacancy
# created by Comercial/Proyecto. Profile templates (org.profile_templates.*,
# org.profile_template_items.*) are owned exclusively by Comercial/Proyecto and are
# intentionally NOT granted to TH either.
TALENTO_HUMANO_PERMISSION_CODES: frozenset[str] = frozenset(
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
        # org — process/stage configuration; read-only on departments/clients/contacts
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

# Internal staff roles — Comercial and Proyecto share an identical allowlist.
# Comercial manages client-side vacancy sourcing; Proyecto manages delivery execution.
# Both roles: create vacancies (but cannot update/delete/publish — TH owns lifecycle),
# read-only pipeline access (applications/notes/interviews/documents), full CRUD on
# the talent pool (they review CVs from other vacancies' applicants to reach out for
# openings the candidate never applied to), and full CRUD on profile templates and
# items (they own candidate profile authoring). Read-only on org.departments and
# org.client_companies — needed to populate the vacancy-creation form's cliente/
# departamento fields and the vacancies list filters; they don't manage those
# catalogs. No org.processes/process_stages (TH owns those). No ai.* permissions.
COMERCIAL_PERMISSION_CODES: frozenset[str] = frozenset(
    {
        # recruitment — vacancy read+create only; full pipeline read; no write on pipeline
        "recruitment.vacancies.read",
        "recruitment.vacancies.create",
        "recruitment.candidates.read",
        "recruitment.applications.read",
        "recruitment.application_notes.read",
        "recruitment.application_documents.read",
        "recruitment.interviews.read",
        # org — departments/clients read-only; contacts read; full CRUD on profile
        # templates/items; parameters read+create+update
        "org.departments.read",
        "org.client_companies.read",
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
        # talent — full CRUD
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

# Proyecto mirrors Comercial exactly — both roles have identical allowlists per design #388.
PROYECTO_PERMISSION_CODES: frozenset[str] = COMERCIAL_PERMISSION_CODES

# Per-role allowlist of org.parameters catalog TYPES (org.parameters.type values)
# each internal role may create/update/delete via POST/PATCH/DELETE /org/parameters.
# Replaces the previous hardcoded, global "non-admins may only write vacancy_name"
# rule — see auth.role_parameter_type_grants and ParameterTypeForbiddenError.
# Talento Humano owns EXACTLY stage/stage_status (the 2 catalogs it operates day to
# day) — vacancy_name ("Plantillas de nombre") is owned by Comercial/Proyecto, not TH.
TALENTO_HUMANO_PARAMETER_TYPES: frozenset[str] = frozenset({"stage", "stage_status"})
COMERCIAL_PARAMETER_TYPES: frozenset[str] = frozenset({"vacancy_name"})

# Proyecto mirrors Comercial exactly, same rationale as PROYECTO_PERMISSION_CODES.
PROYECTO_PARAMETER_TYPES: frozenset[str] = COMERCIAL_PARAMETER_TYPES


class BootstrapError(Exception):
    """Raised when the environment is not ready to bootstrap (e.g. missing seed)."""


@dataclass
class BootstrapResult:
    permissions_synced: int
    grants: int
    role_id: int
    user_id: int
    user_created: bool


async def sync_permissions(session: AsyncSession) -> int:
    """Upsert the whole catalog into auth.permissions, refreshing name/module."""
    rows = [
        {"code": s.code, "name": s.name, "module": s.module, "is_active": True}
        for s in PERMISSION_CATALOG
    ]
    stmt = pg_insert(Permission).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Permission.code],
        set_={
            "name": stmt.excluded.name,
            "module": stmt.excluded.module,
            "is_active": True,
        },
    )
    await session.execute(stmt)
    return len(rows)


async def ensure_admin_role(session: AsyncSession) -> Role:
    stmt = select(Role).where(Role.name == ADMIN_ROLE_NAME).where(Role.is_active.is_(True))
    role = (await session.execute(stmt)).scalar_one_or_none()
    if role is None:
        role = Role(name=ADMIN_ROLE_NAME, description="Full access — all permissions")
        session.add(role)
        await session.flush()
    return role


async def grant_all_permissions_to_role(session: AsyncSession, role_id: int) -> int:
    permission_ids = (
        (await session.execute(select(Permission.id).where(Permission.is_active.is_(True))))
        .scalars()
        .all()
    )
    rows = [{"role_id": role_id, "permission_id": pid, "is_active": True} for pid in permission_ids]
    stmt = pg_insert(RolePermission).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[RolePermission.role_id, RolePermission.permission_id],
        set_={"is_active": True},
    )
    await session.execute(stmt)
    return len(rows)


async def ensure_candidate_role(session: AsyncSession) -> Role:
    stmt = select(Role).where(Role.name == CANDIDATE_ROLE_NAME).where(Role.is_active.is_(True))
    role = (await session.execute(stmt)).scalar_one_or_none()
    if role is None:
        role = Role(
            name=CANDIDATE_ROLE_NAME,
            description="Candidate portal — restricted self-service permissions",
        )
        session.add(role)
        await session.flush()
    return role


async def grant_permissions_to_role(
    session: AsyncSession, role_id: int, allowlist: frozenset[str]
) -> int:
    """Make a role hold EXACTLY the permissions named in *allowlist*.

    Upserts active grants for every code in *allowlist* and revokes (is_active=False)
    any existing active grant on this role whose permission code is NOT in the list.
    Safe to call multiple times — the result is always the same stable state.

    Returns the number of grants in the allowlist (may differ from the number of
    rows actually changed).
    """
    permission_ids = (
        (await session.execute(select(Permission.id).where(Permission.code.in_(allowlist))))
        .scalars()
        .all()
    )
    if not permission_ids:
        return 0
    rows = [{"role_id": role_id, "permission_id": pid, "is_active": True} for pid in permission_ids]
    stmt = pg_insert(RolePermission).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[RolePermission.role_id, RolePermission.permission_id],
        set_={"is_active": True},
    )
    await session.execute(stmt)

    # Authoritative: revoke any grant on this role outside the allowlist so a
    # previously-granted permission is actually removed, not just left behind.
    await session.execute(
        update(RolePermission)
        .where(RolePermission.role_id == role_id)
        .where(RolePermission.permission_id.not_in(permission_ids))
        .where(RolePermission.is_active.is_(True))
        .values(is_active=False)
    )
    return len(rows)


async def grant_parameter_types_to_role(
    session: AsyncSession, role_id: int, allowlist: set[str]
) -> int:
    """Make a role's writable org.parameters TYPE allowlist match *allowlist* exactly.

    Upserts active grants for every type in *allowlist* and revokes (is_active=False)
    any existing active grant on this role whose parameter_type is NOT in the list.
    Mirrors grant_permissions_to_role's upsert-then-revoke shape; unlike permissions,
    parameter_type is a plain string (org.parameters.type is not a modeled entity),
    so there is no id lookup step — the allowlist strings ARE the rows.

    Safe to call multiple times — the result is always the same stable state. An
    empty allowlist revokes every active grant on the role (fail-closed).

    Returns the number of grants in the allowlist.
    """
    if allowlist:
        rows = [
            {"role_id": role_id, "parameter_type": ptype, "is_active": True}
            for ptype in allowlist
        ]
        stmt = pg_insert(RoleParameterTypeGrant).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=[
                RoleParameterTypeGrant.role_id,
                RoleParameterTypeGrant.parameter_type,
            ],
            set_={"is_active": True},
        )
        await session.execute(stmt)

    # Authoritative: revoke any grant on this role outside the allowlist so a
    # previously-granted type is actually removed, not just left behind.
    revoke_stmt = (
        update(RoleParameterTypeGrant)
        .where(RoleParameterTypeGrant.role_id == role_id)
        .where(RoleParameterTypeGrant.is_active.is_(True))
        .values(is_active=False)
    )
    if allowlist:
        revoke_stmt = revoke_stmt.where(
            RoleParameterTypeGrant.parameter_type.not_in(allowlist)
        )
    await session.execute(revoke_stmt)
    return len(allowlist)


async def grant_candidate_permissions_to_role(session: AsyncSession, role_id: int) -> int:
    """Make the candidate role hold EXACTLY CANDIDATE_PERMISSION_CODES.

    Thin wrapper over :func:`grant_permissions_to_role` kept for backwards
    compatibility.
    """
    return await grant_permissions_to_role(session, role_id, CANDIDATE_PERMISSION_CODES)


async def ensure_admin_user(
    session: AsyncSession, email: str, password: str, portal_id: int
) -> tuple[User, bool]:
    existing = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if existing is not None:
        return existing, False
    user = User(
        email=email,
        password_hash=hash_password(password),
        portal_id=portal_id,
        email_verified=True,
        is_active=True,
    )
    session.add(user)
    await session.flush()
    return user, True


async def assign_role_to_user(session: AsyncSession, user_id: int, role_id: int) -> None:
    stmt = pg_insert(UserRole).values(user_id=user_id, role_id=role_id, is_active=True)
    stmt = stmt.on_conflict_do_update(
        index_elements=[UserRole.user_id, UserRole.role_id],
        set_={"is_active": True},
    )
    await session.execute(stmt)


async def bootstrap_admin(session: AsyncSession, email: str, password: str) -> BootstrapResult:
    """Sync permissions, ensure the admin role with all grants, and the admin user."""
    portal = await ParameterRepository(session).get_by_type_and_code(
        "user_portal", ADMIN_PORTAL_CODE
    )
    if portal is None:
        raise BootstrapError(
            "user_portal:staff parameter not found — run `alembic upgrade head` first"
        )

    permissions = await sync_permissions(session)
    role = await ensure_admin_role(session)
    grants = await grant_all_permissions_to_role(session, role.id)
    user, created = await ensure_admin_user(session, email, password, portal.id)
    await assign_role_to_user(session, user.id, role.id)

    # Candidate role — idempotent; must exist before any candidate registers.
    candidate_role = await ensure_candidate_role(session)
    await grant_candidate_permissions_to_role(session, candidate_role.id)

    # Internal staff roles — created idempotently and granted their exact allowlists
    # (both the permission-code allowlist and the org.parameters TYPE allowlist).
    _internal_roles: list[tuple[str, str, frozenset[str], frozenset[str]]] = [
        (
            TALENTO_HUMANO_ROLE_NAME,
            "HR and recruitment management",
            TALENTO_HUMANO_PERMISSION_CODES,
            TALENTO_HUMANO_PARAMETER_TYPES,
        ),
        (
            COMERCIAL_ROLE_NAME,
            "Commercial team — client-driven recruitment",
            COMERCIAL_PERMISSION_CODES,
            COMERCIAL_PARAMETER_TYPES,
        ),
        (
            PROYECTO_ROLE_NAME,
            "Project team — delivery-side recruitment",
            PROYECTO_PERMISSION_CODES,
            PROYECTO_PARAMETER_TYPES,
        ),
    ]
    for rname, rdesc, rcodes, rparam_types in _internal_roles:
        stmt = select(Role).where(Role.name == rname).where(Role.is_active.is_(True))
        internal_role = (await session.execute(stmt)).scalar_one_or_none()
        if internal_role is None:
            internal_role = Role(name=rname, description=rdesc)
            session.add(internal_role)
            await session.flush()
        await grant_permissions_to_role(session, internal_role.id, rcodes)
        await grant_parameter_types_to_role(session, internal_role.id, set(rparam_types))

    return BootstrapResult(
        permissions_synced=permissions,
        grants=grants,
        role_id=role.id,
        user_id=user.id,
        user_created=created,
    )
