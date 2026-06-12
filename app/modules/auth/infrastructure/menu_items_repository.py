from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.infrastructure.models import MenuItem
from app.shared.repository import BaseRepository


class MenuItemRepository(BaseRepository[MenuItem]):
    """Repository for auth.menu_items — adds portal-scoped, order-sorted listing."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, MenuItem)

    async def list_by_portal(self, portal_id: int) -> list[MenuItem]:
        stmt = (
            select(MenuItem)
            .where(MenuItem.portal_id == portal_id)
            .where(MenuItem.is_active.is_(True))
            .order_by(MenuItem.order, MenuItem.id)
        )
        return list((await self.session.execute(stmt)).scalars().all())
