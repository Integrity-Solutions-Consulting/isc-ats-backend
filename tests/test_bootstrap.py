import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.application.bootstrap_service import (
    ADMIN_ROLE_NAME,
    CANDIDATE_PERMISSION_CODES,
    CANDIDATE_ROLE_NAME,
    bootstrap_admin,
)
from app.modules.auth.infrastructure.authorization_repository import (
    AuthorizationRepository,
)
from app.modules.auth.infrastructure.models import Permission, Role, RolePermission
from app.modules.auth.permissions_catalog import ALL_CODES, PERMISSION_CATALOG


def _email() -> str:
    return f"admin-{uuid.uuid4().hex[:12]}@isc.local"


def test_catalog_codes_are_unique_and_well_formed() -> None:
    codes = [s.code for s in PERMISSION_CATALOG]
    assert len(codes) == len(set(codes))
    assert len(ALL_CODES) == len(PERMISSION_CATALOG)
    for spec in PERMISSION_CATALOG:
        assert spec.code.startswith(f"{spec.module}.")
        assert spec.name


async def test_bootstrap_gives_admin_every_permission(session: AsyncSession) -> None:
    result = await bootstrap_admin(session, _email(), "S3cret-pass")

    assert result.user_created is True
    assert result.permissions_synced == len(PERMISSION_CATALOG)
    assert result.grants == len(PERMISSION_CATALOG)

    codes = await AuthorizationRepository(session).list_permission_codes_for_user(
        result.user_id
    )
    assert codes == set(ALL_CODES)


async def test_bootstrap_is_idempotent(session: AsyncSession) -> None:
    email = _email()
    first = await bootstrap_admin(session, email, "S3cret-pass")
    second = await bootstrap_admin(session, email, "S3cret-pass")

    # Same admin user and role reused; permissions still complete.
    assert second.user_created is False
    assert second.user_id == first.user_id
    assert second.role_id == first.role_id

    codes = await AuthorizationRepository(session).list_permission_codes_for_user(
        first.user_id
    )
    assert codes == set(ALL_CODES)


def test_admin_role_name_is_stable() -> None:
    assert ADMIN_ROLE_NAME == "Administrador"


def test_candidate_role_name_is_stable() -> None:
    assert CANDIDATE_ROLE_NAME == "candidate"


def test_candidate_permission_codes_are_the_expected_set() -> None:
    """The candidate permission set must match the BFF contract exactly."""
    expected = frozenset(
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
    assert CANDIDATE_PERMISSION_CODES == expected


async def test_bootstrap_creates_candidate_role_with_correct_grants(
    session: AsyncSession,
) -> None:
    """After bootstrap the candidate role exists and holds exactly CANDIDATE_PERMISSION_CODES."""
    await bootstrap_admin(session, f"admin-{uuid.uuid4().hex[:10]}@test.local", "S3cret")

    role = (
        await session.execute(
            select(Role).where(Role.name == CANDIDATE_ROLE_NAME).where(Role.is_active.is_(True))
        )
    ).scalar_one_or_none()
    assert role is not None, "candidate role must exist after bootstrap"

    granted_codes = set(
        (
            await session.execute(
                select(Permission.code)
                .join(RolePermission, RolePermission.permission_id == Permission.id)
                .where(RolePermission.role_id == role.id)
                .where(RolePermission.is_active.is_(True))
            )
        ).scalars().all()
    )
    assert granted_codes == set(CANDIDATE_PERMISSION_CODES)


async def test_bootstrap_candidate_role_is_idempotent(session: AsyncSession) -> None:
    """Running bootstrap twice must not duplicate or drop candidate grants."""
    email = f"admin-{uuid.uuid4().hex[:10]}@test.local"
    await bootstrap_admin(session, email, "S3cret")
    await bootstrap_admin(session, email, "S3cret")

    role = (
        await session.execute(
            select(Role).where(Role.name == CANDIDATE_ROLE_NAME).where(Role.is_active.is_(True))
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
        ).scalars().all()
    )
    assert granted_codes == set(CANDIDATE_PERMISSION_CODES)
