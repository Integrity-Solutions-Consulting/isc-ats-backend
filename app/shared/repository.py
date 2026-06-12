from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.base_model import Base
from app.shared.pagination import PageParams


class BaseRepository[ModelT: Base]:
    """Generic async repository for soft-deletable models.

    Thin-layer modules use this directly; the ORM model IS the persistence model
    (no separate domain entity / mapper). Override or extend per module when a
    query needs more than plain CRUD.
    """

    def __init__(self, session: AsyncSession, model: type[ModelT]) -> None:
        self.session = session
        self.model = model

    async def get(self, entity_id: int, *, include_inactive: bool = False) -> ModelT | None:
        stmt = select(self.model).where(self.model.id == entity_id)
        if not include_inactive:
            stmt = stmt.where(self.model.is_active.is_(True))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list(
        self,
        params: PageParams,
        *,
        filters: dict[str, Any] | None = None,
        include_inactive: bool = False,
    ) -> tuple[list[ModelT], int]:
        stmt = select(self.model)
        if not include_inactive:
            stmt = stmt.where(self.model.is_active.is_(True))
        for field, value in (filters or {}).items():
            stmt = stmt.where(getattr(self.model, field) == value)

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await self.session.execute(count_stmt)).scalar_one()

        stmt = stmt.order_by(self.model.id).offset(params.offset).limit(params.limit)
        items = list((await self.session.execute(stmt)).scalars().all())
        return items, total

    async def add(self, entity: ModelT) -> ModelT:
        self.session.add(entity)
        await self.session.flush()
        await self.session.refresh(entity)
        return entity

    async def update(self, entity: ModelT, data: dict[str, Any]) -> ModelT:
        for field, value in data.items():
            setattr(entity, field, value)
        await self.session.flush()
        await self.session.refresh(entity)
        return entity

    async def soft_delete(self, entity: ModelT) -> None:
        """Logical delete — sets is_active = False. Never removes the row."""
        entity.is_active = False
        await self.session.flush()
