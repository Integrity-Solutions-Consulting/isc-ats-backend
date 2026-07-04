"""Idempotent bootstrap of the RBAC baseline: permission catalog, admin role, admin user.

Runs the same way every time — re-invoking against an already-bootstrapped database
only refreshes permission metadata and leaves existing rows intact. Driven by the
permissions_catalog (source of truth), so no static seed data lives in migrations.
"""

from dataclasses import dataclass

from sqlalchemy import select
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
        "recruitment.vacancies.read",
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


async def grant_candidate_permissions_to_role(session: AsyncSession, role_id: int) -> int:
    """Upsert exactly CANDIDATE_PERMISSION_CODES grants for the candidate role."""
    permission_ids = (
        await session.execute(
            select(Permission.id).where(Permission.code.in_(CANDIDATE_PERMISSION_CODES))
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

    # Ensure other standard roles exist
    for rname in ["Talento Humano", "Comercial", "Proyecto"]:
        stmt = select(Role).where(Role.name == rname).where(Role.is_active.is_(True))
        existing_role = (await session.execute(stmt)).scalar_one_or_none()
        if existing_role is None:
            session.add(Role(name=rname, description=f"Rol de {rname}"))
    await session.flush()

    return BootstrapResult(
        permissions_synced=permissions,
        grants=grants,
        role_id=role.id,
        user_id=user.id,
        user_created=created,
    )
