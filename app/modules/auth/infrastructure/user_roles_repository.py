from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.infrastructure.models import Role, UserRole


class UserRoleRepository:
    """Repository for the auth.user_roles junction (composite key, no surrogate id).

    Does not extend BaseRepository: that helper assumes a single `id` column.
    Re-assigning a previously revoked link reactivates the existing row instead of
    inserting a duplicate primary key.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(
        self, user_id: int, role_id: int, *, include_inactive: bool = False
    ) -> UserRole | None:
        stmt = (
            select(UserRole)
            .where(UserRole.user_id == user_id)
            .where(UserRole.role_id == role_id)
        )
        if not include_inactive:
            stmt = stmt.where(UserRole.is_active.is_(True))
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def add(self, link: UserRole) -> UserRole:
        self.session.add(link)
        await self.session.flush()
        await self.session.refresh(link)
        return link

    async def save(self, link: UserRole) -> UserRole:
        """Persist in-place mutations (e.g. reactivation) on an existing link."""
        await self.session.flush()
        await self.session.refresh(link)
        return link

    async def soft_delete(self, link: UserRole) -> None:
        link.is_active = False
        await self.session.flush()

    async def list_active_links_for_user(self, user_id: int) -> list[UserRole]:
        """Active UserRole link rows (not resolved Role objects) — needed to
        soft-delete them individually, e.g. when replacing a user's role."""
        stmt = (
            select(UserRole)
            .where(UserRole.user_id == user_id)
            .where(UserRole.is_active.is_(True))
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_roles_for_user(self, user_id: int) -> list[Role]:
        stmt = (
            select(Role)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user_id)
            .where(UserRole.is_active.is_(True))
            .where(Role.is_active.is_(True))
            .order_by(Role.id)
        )
        return list((await self.session.execute(stmt)).scalars().all())
