from typing import Any

from app.core.dependencies import CurrentUser
from app.modules.auth.infrastructure.models import User
from app.modules.org.infrastructure.models import Parameter, ProcessStage
from app.modules.recruitment.api.interviews_schemas import (
    InterviewCreate,
    InterviewUpdate,
)
from app.modules.recruitment.infrastructure.application_models import Application
from app.modules.recruitment.infrastructure.interview_models import Interview
from app.shared.pagination import PageParams
from app.shared.repository import BaseRepository


class InterviewNotFoundError(Exception):
    pass


class InterviewReferenceError(Exception):
    """A referenced application, stage, interviewer or parameter does not exist."""


class InterviewValidationError(Exception):
    """The interview window is invalid (ends_at not after scheduled_at)."""


class InterviewService:
    """CRUD for recruitment.interviews, validating its references and time window.

    `interviewer_id` is a user; `status_id` (interview_status) and
    `scheduled_by_id` (interview_scheduler) are org.parameters.
    """

    def __init__(
        self,
        repository: BaseRepository[Interview],
        applications: BaseRepository[Application],
        process_stages: BaseRepository[ProcessStage],
        users: BaseRepository[User],
        parameters: BaseRepository[Parameter],
    ) -> None:
        self.repository = repository
        self.applications = applications
        self.process_stages = process_stages
        self.users = users
        self.parameters = parameters

    async def list(
        self, params: PageParams, *, application_id: int | None = None
    ) -> tuple[list[Interview], int]:
        filters = {"application_id": application_id} if application_id else None
        return await self.repository.list(params, filters=filters)

    async def get(self, interview_id: int) -> Interview:
        interview = await self.repository.get(interview_id)
        if interview is None:
            raise InterviewNotFoundError(f"Interview {interview_id} not found")
        return interview

    async def create(self, data: InterviewCreate, actor: CurrentUser) -> Interview:
        if data.ends_at <= data.scheduled_at:
            raise InterviewValidationError("ends_at must be after scheduled_at")
        await self._validate_refs(data.model_dump())
        interview = Interview(
            **data.model_dump(),
            created_by=actor.user_id,
            ip_created=actor.ip,
        )
        return await self.repository.add(interview)

    async def update(
        self, interview_id: int, data: InterviewUpdate, actor: CurrentUser
    ) -> Interview:
        interview = await self.get(interview_id)
        changes = data.model_dump(exclude_unset=True)
        scheduled = changes.get("scheduled_at", interview.scheduled_at)
        ends = changes.get("ends_at", interview.ends_at)
        if ends <= scheduled:
            raise InterviewValidationError("ends_at must be after scheduled_at")
        await self._validate_refs(changes)
        changes["updated_by"] = actor.user_id
        changes["ip_updated"] = actor.ip
        return await self.repository.update(interview, changes)

    async def delete(self, interview_id: int) -> None:
        interview = await self.get(interview_id)
        await self.repository.soft_delete(interview)

    async def _validate_refs(self, values: dict[str, Any]) -> None:
        await self._assert(self.applications, values.get("application_id"), "application_id")
        await self._assert(self.process_stages, values.get("process_stage_id"), "process_stage_id")
        await self._assert(self.users, values.get("interviewer_id"), "interviewer_id")
        await self._assert(self.parameters, values.get("status_id"), "status_id")
        await self._assert(self.parameters, values.get("scheduled_by_id"), "scheduled_by_id")

    async def _assert(
        self, repo: BaseRepository[Any], entity_id: int | None, label: str
    ) -> None:
        if entity_id is not None and await repo.get(entity_id) is None:
            raise InterviewReferenceError(f"{label}={entity_id} not found")
