from app.core.dependencies import CurrentUser
from app.modules.comms.api.email_logs_schemas import EmailLogCreate
from app.modules.comms.infrastructure.models import EmailLog
from app.modules.org.infrastructure.models import Parameter
from app.shared.pagination import PageParams
from app.shared.repository import BaseRepository


class EmailLogNotFoundError(Exception):
    pass


class EmailLogReferenceError(Exception):
    pass


class EmailLogService:
    def __init__(
        self,
        repository: BaseRepository[EmailLog],
        parameters: BaseRepository[Parameter],
    ) -> None:
        self.repository = repository
        self.parameters = parameters

    async def list(
        self,
        params: PageParams,
        *,
        status_id: int | None = None,
    ) -> tuple[list[EmailLog], int]:
        filters = {"status_id": status_id} if status_id is not None else None
        return await self.repository.list(params, filters=filters)

    async def get(self, log_id: int) -> EmailLog:
        log = await self.repository.get(log_id)
        if log is None:
            raise EmailLogNotFoundError(f"Email log {log_id} not found")
        return log

    async def create(self, data: EmailLogCreate, actor: CurrentUser) -> EmailLog:
        if await self.parameters.get(data.status_id) is None:
            raise EmailLogReferenceError(f"status_id={data.status_id} not found")
        log = EmailLog(
            **data.model_dump(),
            created_by=actor.user_id,
            ip_created=actor.ip,
        )
        return await self.repository.add(log)
