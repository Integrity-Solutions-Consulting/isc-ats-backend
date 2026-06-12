from app.core.dependencies import CurrentUser
from app.modules.auth.infrastructure.models import User
from app.modules.recruitment.api.interviewer_availability_schemas import (
    AvailabilityCreate,
    AvailabilityUpdate,
)
from app.modules.recruitment.infrastructure.interview_models import (
    InterviewerAvailability,
)
from app.shared.pagination import PageParams
from app.shared.repository import BaseRepository


class AvailabilityNotFoundError(Exception):
    pass


class AvailabilityReferenceError(Exception):
    """The referenced user does not exist."""


class AvailabilityValidationError(Exception):
    """The window is invalid (e.g. end_time not after start_time)."""


class InterviewerAvailabilityService:
    """Thin CRUD for recruitment.interviewer_availability (validates user + window)."""

    def __init__(
        self,
        repository: BaseRepository[InterviewerAvailability],
        users: BaseRepository[User],
    ) -> None:
        self.repository = repository
        self.users = users

    async def list(
        self, params: PageParams, *, user_id: int | None = None
    ) -> tuple[list[InterviewerAvailability], int]:
        filters = {"user_id": user_id} if user_id else None
        return await self.repository.list(params, filters=filters)

    async def get(self, availability_id: int) -> InterviewerAvailability:
        availability = await self.repository.get(availability_id)
        if availability is None:
            raise AvailabilityNotFoundError(
                f"Availability {availability_id} not found"
            )
        return availability

    async def create(
        self, data: AvailabilityCreate, actor: CurrentUser
    ) -> InterviewerAvailability:
        if await self.users.get(data.user_id) is None:
            raise AvailabilityReferenceError(f"user_id={data.user_id} not found")
        if data.end_time <= data.start_time:
            raise AvailabilityValidationError("end_time must be after start_time")
        availability = InterviewerAvailability(
            **data.model_dump(),
            created_by=actor.user_id,
            ip_created=actor.ip,
        )
        return await self.repository.add(availability)

    async def update(
        self, availability_id: int, data: AvailabilityUpdate, actor: CurrentUser
    ) -> InterviewerAvailability:
        availability = await self.get(availability_id)
        changes = data.model_dump(exclude_unset=True)
        start = changes.get("start_time", availability.start_time)
        end = changes.get("end_time", availability.end_time)
        if end <= start:
            raise AvailabilityValidationError("end_time must be after start_time")
        changes["updated_by"] = actor.user_id
        changes["ip_updated"] = actor.ip
        return await self.repository.update(availability, changes)

    async def delete(self, availability_id: int) -> None:
        availability = await self.get(availability_id)
        await self.repository.soft_delete(availability)
