from app.core.dependencies import CurrentUser
from app.modules.ai.api.ai_usage_logs_schemas import AiUsageLogCreate
from app.modules.ai.infrastructure.models import AiUsageLog
from app.shared.pagination import PageParams
from app.shared.repository import BaseRepository


class AiUsageLogNotFoundError(Exception):
    pass


class AiUsageLogService:
    def __init__(self, repository: BaseRepository[AiUsageLog]) -> None:
        self.repository = repository

    async def list(
        self,
        params: PageParams,
        *,
        action: str | None = None,
    ) -> tuple[list[AiUsageLog], int]:
        filters = {"action": action} if action is not None else None
        return await self.repository.list(params, filters=filters)

    async def get(self, log_id: int) -> AiUsageLog:
        log = await self.repository.get(log_id)
        if log is None:
            raise AiUsageLogNotFoundError(f"AI usage log {log_id} not found")
        return log

    async def create(self, data: AiUsageLogCreate, actor: CurrentUser) -> AiUsageLog:
        log = AiUsageLog(
            **data.model_dump(),
            created_by=actor.user_id,
            ip_created=actor.ip,
        )
        return await self.repository.add(log)
