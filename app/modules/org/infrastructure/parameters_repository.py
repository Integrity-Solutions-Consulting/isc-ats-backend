from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.org.infrastructure.models import Parameter
from app.shared.repository import BaseRepository


class ParameterRepository(BaseRepository[Parameter]):
    """Repository for the polymorphic org.parameters catalog."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Parameter)

    async def get_by_type_and_code(self, type_: str, code: str) -> Parameter | None:
        stmt = (
            select(Parameter)
            .where(Parameter.type == type_)
            .where(Parameter.code == code)
            .where(Parameter.is_active.is_(True))
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
