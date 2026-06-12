from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.org.infrastructure.models import Process
from app.shared.repository import BaseRepository


class ProcessRepository(BaseRepository[Process]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Process)

    async def find_duplicate(
        self,
        client_company_id: int,
        department_id: int,
        name: str,
        *,
        exclude_id: int | None = None,
    ) -> Process | None:
        """An active process with the same (company, department, name).

        `exclude_id` skips the row being updated so it doesn't clash with itself.
        """
        stmt = (
            select(Process)
            .where(Process.client_company_id == client_company_id)
            .where(Process.department_id == department_id)
            .where(Process.name == name)
            .where(Process.is_active.is_(True))
        )
        if exclude_id is not None:
            stmt = stmt.where(Process.id != exclude_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()
