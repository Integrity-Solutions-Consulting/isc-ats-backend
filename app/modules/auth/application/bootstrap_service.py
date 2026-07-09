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
    RolePermission,
    User,
    UserRole,
)
from app.modules.auth.permissions_catalog import PERMISSION_CATALOG
from app.modules.org.infrastructure.parameters_repository import ParameterRepository

ADMIN_ROLE_NAME = "Administrador"
ADMIN_PORTAL_CODE = "staff"

CANDIDATE_ROLE_NAME = "candidate"

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

# Internal staff role — Talento Humano.  Full access to the recruitment pipeline,
# org configuration management, talent pool, storage, and AI tooling.
TALENTO_HUMANO_PERMISSION_CODES: frozenset[str] = frozenset(
    {
        # org — full CRUD on all configuration entities
        "org.parameters.read",
        "org.departments.read",
        "org.departments.create",
        "org.departments.update",
        "org.departments.delete",
        "org.client_companies.read",
        "org.client_companies.create",
        "org.client_companies.update",
        "org.client_companies.delete",
        "org.contacts.read",
        "org.contacts.create",
        "org.contacts.update",
        "org.contacts.delete",
        "org.processes.read",
        "org.processes.create",
        "org.processes.update",
        "org.processes.delete",
        "org.process_stages.read",
        "org.process_stages.create",
        "org.process_stages.update",
        "org.process_stages.delete",
        "org.profile_templates.read",
        "org.profile_templates.create",
        "org.profile_templates.update",
        "org.profile_templates.delete",
        "org.profile_template_items.read",
        "org.profile_template_items.create",
        "org.profile_template_items.update",
        "org.profile_template_items.delete",
        # recruitment — full pipeline management
        "recruitment.vacancies.read",
        "recruitment.vacancies.create",
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
        "recruitment.application_documents.read",
        "recruitment.application_documents.create",
        "recruitment.application_documents.update",
        "recruitment.application_documents.delete",
        "recruitment.application_notes.read",
        "recruitment.application_notes.create",
        "recruitment.application_notes.update",
        "recruitment.application_notes.delete",
        "recruitment.interviews.read",
        "recruitment.interviews.create",
        "recruitment.interviews.update",
        "recruitment.interviews.delete",
        "recruitment.interviewer_availability.read",
        "recruitment.interviewer_availability.create",
        "recruitment.interviewer_availability.update",
        "recruitment.interviewer_availability.delete",
        # talent
        "talent.talent_pool.read",
        "talent.talent_pool.create",
        "talent.talent_pool.delete",
        # storage
        "storage.files.read",
        "storage.files.create",
        "storage.files.update",
        "storage.files.delete",
        # ai
        "ai.cv_parse_jobs.read",
        "ai.cv_parse_jobs.create",
        "ai.cv_parse_jobs.update",
        "ai.cv_parse_jobs.delete",
        "ai.vacancy_promo_images.read",
        "ai.vacancy_promo_images.create",
        "ai.vacancy_promo_images.delete",
        "ai.ai_usage_logs.read",
        "ai.ai_usage_logs.create",
    }
)

# Internal staff roles — Comercial and Proyecto share an identical allowlist.
# Comercial drives client-side requirements; Proyecto manages delivery execution.
# Both need full recruitment pipeline access but only read-only access to org config.
COMERCIAL_PERMISSION_CODES: frozenset[str] = frozenset(
    {
        # org — read-only view of org structures (no configuration mutations)
        "org.parameters.read",
        "org.departments.read",
        "org.client_companies.read",
        "org.contacts.read",
        "org.processes.read",
        "org.process_stages.read",
        "org.profile_templates.read",
        "org.profile_template_items.read",
        # recruitment — full pipeline management (same as TH)
        "recruitment.vacancies.read",
        "recruitment.vacancies.create",
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
        "recruitment.application_documents.read",
        "recruitment.application_documents.create",
        "recruitment.application_documents.update",
        "recruitment.application_documents.delete",
        "recruitment.application_notes.read",
        "recruitment.application_notes.create",
        "recruitment.application_notes.update",
        "recruitment.application_notes.delete",
        "recruitment.interviews.read",
        "recruitment.interviews.create",
        "recruitment.interviews.update",
        "recruitment.interviews.delete",
        "recruitment.interviewer_availability.read",
        "recruitment.interviewer_availability.create",
        "recruitment.interviewer_availability.update",
        "recruitment.interviewer_availability.delete",
        # talent
        "talent.talent_pool.read",
        "talent.talent_pool.create",
        "talent.talent_pool.delete",
        # storage
        "storage.files.read",
        "storage.files.create",
        "storage.files.update",
        "storage.files.delete",
        # ai
        "ai.cv_parse_jobs.read",
        "ai.cv_parse_jobs.create",
        "ai.cv_parse_jobs.update",
        "ai.cv_parse_jobs.delete",
        "ai.vacancy_promo_images.read",
        "ai.vacancy_promo_images.create",
        "ai.vacancy_promo_images.delete",
        "ai.ai_usage_logs.read",
        "ai.ai_usage_logs.create",
    }
)

# Proyecto mirrors Comercial exactly — both roles have identical pipeline access.
PROYECTO_PERMISSION_CODES: frozenset[str] = COMERCIAL_PERMISSION_CODES


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
    stmt = (
        select(Role)
        .where(Role.name == ADMIN_ROLE_NAME)
        .where(Role.is_active.is_(True))
    )
    role = (await session.execute(stmt)).scalar_one_or_none()
    if role is None:
        role = Role(name=ADMIN_ROLE_NAME, description="Full access — all permissions")
        session.add(role)
        await session.flush()
    return role


async def grant_all_permissions_to_role(session: AsyncSession, role_id: int) -> int:
    permission_ids = (
        await session.execute(
            select(Permission.id).where(Permission.is_active.is_(True))
        )
    ).scalars().all()
    rows = [
        {"role_id": role_id, "permission_id": pid, "is_active": True}
        for pid in permission_ids
    ]
    stmt = pg_insert(RolePermission).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[RolePermission.role_id, RolePermission.permission_id],
        set_={"is_active": True},
    )
    await session.execute(stmt)
    return len(rows)


async def ensure_candidate_role(session: AsyncSession) -> Role:
    stmt = (
        select(Role)
        .where(Role.name == CANDIDATE_ROLE_NAME)
        .where(Role.is_active.is_(True))
    )
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
        await session.execute(
            select(Permission.id).where(Permission.code.in_(allowlist))
        )
    ).scalars().all()
    if not permission_ids:
        return 0
    rows = [
        {"role_id": role_id, "permission_id": pid, "is_active": True}
        for pid in permission_ids
    ]
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


async def grant_candidate_permissions_to_role(session: AsyncSession, role_id: int) -> int:
    """Make the candidate role hold EXACTLY CANDIDATE_PERMISSION_CODES.

    Thin wrapper over :func:`grant_permissions_to_role` kept for backwards
    compatibility.
    """
    return await grant_permissions_to_role(session, role_id, CANDIDATE_PERMISSION_CODES)


async def ensure_admin_user(
    session: AsyncSession, email: str, password: str, portal_id: int
) -> tuple[User, bool]:
    existing = (
        await session.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
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


async def assign_role_to_user(
    session: AsyncSession, user_id: int, role_id: int
) -> None:
    stmt = pg_insert(UserRole).values(
        user_id=user_id, role_id=role_id, is_active=True
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[UserRole.user_id, UserRole.role_id],
        set_={"is_active": True},
    )
    await session.execute(stmt)


async def bootstrap_admin(
    session: AsyncSession, email: str, password: str
) -> BootstrapResult:
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

    # Internal staff roles — created idempotently and granted their exact allowlists.
    _internal_roles: list[tuple[str, str, frozenset[str]]] = [
        ("Talento Humano", "HR and recruitment management", TALENTO_HUMANO_PERMISSION_CODES),
        ("Comercial", "Commercial team — client-driven recruitment", COMERCIAL_PERMISSION_CODES),
        ("Proyecto", "Project team — delivery-side recruitment", PROYECTO_PERMISSION_CODES),
    ]
    for rname, rdesc, rcodes in _internal_roles:
        stmt = select(Role).where(Role.name == rname).where(Role.is_active.is_(True))
        internal_role = (await session.execute(stmt)).scalar_one_or_none()
        if internal_role is None:
            internal_role = Role(name=rname, description=rdesc)
            session.add(internal_role)
            await session.flush()
        await grant_permissions_to_role(session, internal_role.id, rcodes)

    return BootstrapResult(
        permissions_synced=permissions,
        grants=grants,
        role_id=role.id,
        user_id=user.id,
        user_created=created,
    )
