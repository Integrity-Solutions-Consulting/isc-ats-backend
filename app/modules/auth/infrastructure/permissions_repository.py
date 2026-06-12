from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.infrastructure.models import Permission
from app.shared.repository import BaseRepository


class PermissionRepository(BaseRepository[Permission]):
    """Repository for the auth.permissions catalog (lookup by unique code)."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Permission)

    async def get_by_code(self, code: str) -> Permission | None:
        stmt = (
            select(Permission)
            .where(Permission.code == code)
            .where(Permission.is_active.is_(True))
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
