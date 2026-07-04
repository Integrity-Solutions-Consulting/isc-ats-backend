from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.infrastructure.models import (
    Permission,
    Role,
    RolePermission,
    UserRole,
)


class AuthorizationRepository:
    """Resolves the effective permissions of a user for authorization checks.

    Walks user_roles -> role_permissions -> permissions, honouring is_active on
    every hop so a revoked role, grant, or permission stops conferring access.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_permission_codes_for_user(self, user_id: int) -> set[str]:
        stmt = (
            select(Permission.code)
            .select_from(UserRole)
            .join(Role, Role.id == UserRole.role_id)
            .join(RolePermission, RolePermission.role_id == UserRole.role_id)
            .join(Permission, Permission.id == RolePermission.permission_id)
            .where(UserRole.user_id == user_id)
            .where(UserRole.is_active.is_(True))
            .where(Role.is_active.is_(True))
            .where(RolePermission.is_active.is_(True))
            .where(Permission.is_active.is_(True))
            .distinct()
        )
        return set((await self.session.execute(stmt)).scalars().all())

    async def list_permission_ids_for_user(self, user_id: int) -> set[int]:
        stmt = (
            select(Permission.id)
            .select_from(UserRole)
            .join(Role, Role.id == UserRole.role_id)
            .join(RolePermission, RolePermission.role_id == UserRole.role_id)
            .join(Permission, Permission.id == RolePermission.permission_id)
            .where(UserRole.user_id == user_id)
            .where(UserRole.is_active.is_(True))
            .where(Role.is_active.is_(True))
            .where(RolePermission.is_active.is_(True))
            .where(Permission.is_active.is_(True))
            .distinct()
        )
        return set((await self.session.execute(stmt)).scalars().all())
