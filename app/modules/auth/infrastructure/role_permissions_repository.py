from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.infrastructure.models import Permission, RolePermission


class RolePermissionRepository:
    """Repository for the auth.role_permissions junction (composite key, no id).

    Does not extend BaseRepository: that helper assumes a single `id` column.
    Re-granting a previously revoked link reactivates the existing row instead of
    inserting a duplicate primary key.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(
        self, role_id: int, permission_id: int, *, include_inactive: bool = False
    ) -> RolePermission | None:
        stmt = (
            select(RolePermission)
            .where(RolePermission.role_id == role_id)
            .where(RolePermission.permission_id == permission_id)
        )
        if not include_inactive:
            stmt = stmt.where(RolePermission.is_active.is_(True))
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def add(self, link: RolePermission) -> RolePermission:
        self.session.add(link)
        await self.session.flush()
        await self.session.refresh(link)
        return link

    async def save(self, link: RolePermission) -> RolePermission:
        """Persist in-place mutations (e.g. reactivation) on an existing link."""
        await self.session.flush()
        await self.session.refresh(link)
        return link

    async def soft_delete(self, link: RolePermission) -> None:
        link.is_active = False
        await self.session.flush()

    async def list_permissions_for_role(self, role_id: int) -> list[Permission]:
        stmt = (
            select(Permission)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(RolePermission.role_id == role_id)
            .where(RolePermission.is_active.is_(True))
            .where(Permission.is_active.is_(True))
            .order_by(Permission.id)
        )
        return list((await self.session.execute(stmt)).scalars().all())
