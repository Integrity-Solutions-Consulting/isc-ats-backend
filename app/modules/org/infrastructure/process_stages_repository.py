from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.org.infrastructure.models import ProcessStage
from app.shared.repository import BaseRepository


class ProcessStageRepository(BaseRepository[ProcessStage]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, ProcessStage)

    async def list_by_process(self, process_id: int) -> list[ProcessStage]:
        """All active stages of a process, in pipeline order."""
        stmt = (
            select(ProcessStage)
            .where(ProcessStage.process_id == process_id)
            .where(ProcessStage.is_active.is_(True))
            .order_by(ProcessStage.order)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def find_by_stage(
        self, process_id: int, stage_id: int, *, exclude_id: int | None = None
    ) -> ProcessStage | None:
        stmt = (
            select(ProcessStage)
            .where(ProcessStage.process_id == process_id)
            .where(ProcessStage.stage_id == stage_id)
            .where(ProcessStage.is_active.is_(True))
        )
        if exclude_id is not None:
            stmt = stmt.where(ProcessStage.id != exclude_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def find_by_order(
        self, process_id: int, order: int, *, exclude_id: int | None = None
    ) -> ProcessStage | None:
        stmt = (
            select(ProcessStage)
            .where(ProcessStage.process_id == process_id)
            .where(ProcessStage.order == order)
            .where(ProcessStage.is_active.is_(True))
        )
        if exclude_id is not None:
            stmt = stmt.where(ProcessStage.id != exclude_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()
